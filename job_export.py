import json
import os
import tempfile
import hashlib

from datetime import datetime

from pfb.importers.gen3dict import _from_dict
from pfb.reader import PFBReader
from pfb.writer import PFBWriter
from pyspark import SparkConf
from pyspark.sql import SparkSession
import requests

from pelican.dictionary import init_dictionary, DataDictionaryTraversal
from pelican.graphql.guppy_gql import GuppyGQL
from pelican.jobs import export_pfb_job
from pelican.s3 import s3upload_file
from pelican.indexd import indexd_submit
from pelican.mds import metadata_submit_expiration

if __name__ == "__main__":
    node = os.environ["ROOT_NODE"]
    access_token = os.environ["ACCESS_TOKEN"]
    input_data = os.environ["INPUT_DATA"]
    access_format = os.environ["ACCESS_FORMAT"]
    input_data_type = os.environ.get("INPUT_DATA_TYPE", None)
    # the PFB file and indexd/mds records expire after 14 days by default
    record_expiration_days = os.environ.get("RECORD_EXPIRATION_DAYS", 14)

    print("This is the format")
    print(access_format)

    with open("/pelican-creds.json") as pelican_creds_file:
        pelican_creds = json.load(pelican_creds_file)
    required_keys = [
        "manifest_bucket_name",
        "aws_access_key_id",
        "aws_secret_access_key",
    ]
    if access_format == "guid":
        required_keys += ["fence_client_id", "fence_client_secret"]
    for key in required_keys:
        assert pelican_creds.get(key), f"No '{key}' in config"

    input_data = json.loads(input_data)

    gql = GuppyGQL(
        node=node, hostname="http://revproxy-service", access_token=access_token
    )
    case_ids = input_data.get("IDS", None)
    filters = json.dumps({"filter": input_data.get("filter", {})})
    if not case_ids or input_data_type != "UUID":
        case_ids = gql.execute(filters=filters)

    with open("/peregrine-creds.json") as pelican_creds_file:
        peregrine_creds = json.load(pelican_creds_file)

    DB_URL = "jdbc:postgresql://{}/{}".format(
        peregrine_creds["db_host"], peregrine_creds["db_database"]
    )
    DB_USER = peregrine_creds["db_username"]
    DB_PASS = peregrine_creds["db_password"]

    dictionary_url = os.environ["DICTIONARY_URL"]
    dictionary, model = init_dictionary(url=dictionary_url)
    ddt = DataDictionaryTraversal(model)

    # EXTRA_NODES is an optional comma-delimited list of nodes to additionally include in the PFB.
    if os.environ.get("EXTRA_NODES") is not None:
        # Allow user to specify EXTRA_NODES == None by passing an empty string.
        # This is so that BioDataCatalyst PFB exports can specify no extra nodes.
        if os.environ["EXTRA_NODES"].strip() == "":
            extra_nodes = None
        else:
            extra_nodes = [n for n in os.environ["EXTRA_NODES"].split(",")]
    else:
        # Preserved for backwards compatibility:
        # If EXTRA_NODES is not specified, add 'reference_file' node
        # when exporting PFBs from BioDataCatalyst (aka STAGE aka gtex)
        if "gtex" in dictionary_url:
            extra_nodes = ["reference_file", "reference_file_index"]
        else:
            extra_nodes = None

    conf = (
        SparkConf()
        .set("spark.jars", os.environ["POSTGRES_JAR_PATH"])
        .set("spark.driver.memory", "10g")
        .set("spark.executor.memory", "10g")
        .setAppName("pelican")
    )

    spark = SparkSession.builder.config(conf=conf).getOrCreate()

    db = spark.read.format("jdbc").options(
        url=DB_URL, user=DB_USER, password=DB_PASS, driver="org.postgresql.Driver"
    )

    with tempfile.NamedTemporaryFile(mode="w+b", delete=False) as avro_output:
        with PFBWriter(avro_output) as pfb_file:
            _from_dict(pfb_file, dictionary_url)
            filename = pfb_file.name
            # print(f'debug - wrote pfb file at {filename}')

    with tempfile.NamedTemporaryFile(mode="w+b", delete=False) as avro_output:
        with PFBReader(filename) as reader:
            with PFBWriter(avro_output) as pfb_file:
                pfb_file.copy_schema(reader)
                pfb_file.write()
                fname = pfb_file.name
                # print(f'debug - copied pfb file at {fname}')

    # If the input data specifies the root node to use, use
    # that root node. Otherwise fall back to $ROOT_NODE environment variable.
    root_node = input_data.get("root_node")
    if root_node is None:
        root_node = node
    # print(f'debug - root_node: {root_node}')

    # adding aligned_reads_index to the extra nodes on VCF files to make them inline with CRAM files
    if root_node == "simple_germline_variation":
        if extra_nodes is None:
            extra_nodes = ["aligned_reads_index"]
        else:
            extra_nodes.append("aligned_reads_index")
    # print(f'debug - extra_nodes: {extra_nodes}')

    with open(fname, "a+b") as avro_output:
        with PFBReader(filename) as reader:
            with PFBWriter(avro_output) as pfb_file:
                pfb_file.copy_schema(reader)
                # print('debug - starting export_pfb_job')
                export_pfb_job(
                    db,
                    pfb_file,
                    ddt,
                    case_ids,
                    root_node,
                    extra_nodes,
                    True,  # include upward nodes: project, program etc
                )

    avro_filename = "{}.avro".format(
        datetime.now().strftime("export_%Y-%m-%dT%H:%M:%S")
    )
    # print(f'debug - avro_filename: {avro_filename}')
    s3file = s3upload_file(
        pelican_creds["manifest_bucket_name"],
        avro_filename,
        pelican_creds["aws_access_key_id"],
        pelican_creds["aws_secret_access_key"],
        fname,
    )
    # print('debug - uploading to s3')

    if access_format == "guid":
        # calculate md5 sum
        md5_sum = hashlib.md5()
        chunk_size = 8192
        with open(fname, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                md5_sum.update(data)

        md5_digest = md5_sum.hexdigest()

        # get authz fields
        auth_paths = gql._graphql_auth_resource_path(filters=filters)

        authz = []
        if len(auth_paths) > 0:
            for path in auth_paths:
                if path["auth_resource_path"] not in authz:
                    authz.append(path["auth_resource_path"])

        hostname = os.environ["GEN3_HOSTNAME"]
        COMMONS = "https://" + hostname + "/"

        # try sending to indexd
        with open("/indexd-creds.json") as indexd_creds_file:
            indexd_creds = json.load(indexd_creds_file)

        s3_url = "s3://" + pelican_creds["manifest_bucket_name"] + "/" + avro_filename

        # exchange the client ID and secret for an access token
        r = requests.post(
            f"{COMMONS}user/oauth2/token?grant_type=client_credentials&scope=openid",
            auth=(
                pelican_creds["fence_client_id"],
                pelican_creds["fence_client_secret"],
            ),
        )
        if r.status_code != 200:
            raise Exception(
                f"Failed to obtain access token using OIDC client credentials - {r.status_code}:\n{r.text}"
            )
        client_access_token = r.json()["access_token"]

        indexd_record = indexd_submit(
            COMMONS,
            indexd_creds["user_db"]["gdcapi"],
            avro_filename,
            os.stat(fname).st_size,
            [s3_url],
            {"md5": str(md5_digest)},
            authz,
        )

        metadata_submit_expiration(
            hostname=COMMONS,
            guid=indexd_record["did"],
            access_token=client_access_token,
            record_expiration_days=record_expiration_days,
        )

        # send s3 link and information to indexd to create guid and send it back
        print("[out] {}".format(indexd_record["did"]))

    else:
        print("[out] {}".format(s3file))
