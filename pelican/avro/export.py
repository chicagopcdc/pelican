import base64
import json
import tempfile
from collections import defaultdict
from datetime import datetime

from fastavro import writer


def create_avro_from(schema, metadata):
    with tempfile.NamedTemporaryFile(mode="w+b", delete=False) as avro_output:
        name = avro_output.name
        writer(avro_output, schema, metadata)
    return name, schema


def create_node_dict(node_id, node_name, values, node_schema, edges):
    inside = json.loads(values)

    vals = {}

    for k, v in inside.iteritems():
        is_unicode = type(v) == unicode
        current_node_schema = filter(lambda x: x["name"] == k, node_schema["fields"])

        if len(current_node_schema) > 0:
            current_node_schema = current_node_schema[0]
        else:
            print("{} is not in the schema for {}".format(k, node_name))
            continue

        if isinstance(current_node_schema["type"], list):
            is_enum = False
            for x in current_node_schema["type"]:
                if "type" in x:
                    is_enum = is_enum or (x["type"] == "enum")
        elif (
                "type" in current_node_schema["type"]
                and current_node_schema["type"]["type"] == "enum"
        ):
            is_enum = True
        else:
            is_enum = False

        if is_unicode and not is_enum:
            val = str(v)
        elif is_enum:
            val = base64.b64encode(str(v)).rstrip("=")
        else:
            val = v

        vals[str(k)] = val

    node_dict = {
        "id": node_id,
        "name": node_name,
        "object": (node_name, vals),
        "relations": edges[node_id] if node_id in edges else [],
    }

    return node_dict


def split_by_n(input_list, n=1000):
    return [input_list[x:x + n] for x in range(0, len(input_list), n)]


def get_ids_from_table(db, table, ids, id_column):
    data = None

    for ids_chunk in split_by_n(ids):
        current_chunk_data = db \
            .option("query", "SELECT * FROM {} WHERE {} IN ('{}')".format(table, id_column, "','".join(ids_chunk))) \
            .load()

        if data:
            data = data.union(current_chunk_data)
        else:
            data = current_chunk_data

    return data if data else None


def export_avro(spark, schema, metadata, dd_tables, traverse_order, case_ids, db_url, db_user, db_pass, root_node):
    start_time = datetime.now()
    print(start_time)

    node_label, edge_label = dd_tables

    db = spark.read.format("jdbc").options(
        url=db_url, user=db_user, password=db_pass, driver="org.postgresql.Driver"
    )

    it = defaultdict(list)

    for e, v in edge_label.items():
        it[v["src"]].append(e)

    visited = {}

    table_logs = "{:<40}"

    avro_filename, parsed_schema = create_avro_from(schema, metadata)

    current_ids = defaultdict(list)

    prev = root_node
    current_ids[prev] = case_ids

    for k in traverse_order:
        v = it[k]
        if visited.get(k, False):
            continue
        visited[k] = True
        node_edges = defaultdict(list)
        for edge_table in v:
            dst_table_name = edge_label[edge_table]["dst"]
            src_table_name = edge_label[edge_table]["src"]
            edges = get_ids_from_table(db, edge_table, current_ids[dst_table_name], "dst_id")

            if not edges:
                print(table_logs.format(edge_table))
                continue

            edges = edges.rdd.map(
                lambda x: {
                    "src_id": x["src_id"],
                    "dst_id": x["dst_id"],
                    "dst_name": dst_table_name,
                }
            )
            print(table_logs.format(edge_table))

            for e in edges.toLocalIterator():
                node_edges[e["src_id"]].append(
                    {"dst_id": e["dst_id"], "dst_name": e["dst_name"]}
                )

            current_ids[src_table_name].extend(node_edges.keys())

        node_table = "node_" + k.replace("_", "")
        node_name = node_label[node_table]

        node_schema = filter(
            lambda x: x["name"] == node_name, parsed_schema["fields"][2]["type"]
        )[0]

        prev = k

        nodes = get_ids_from_table(db, node_table, current_ids[prev], "node_id")

        if not nodes:
            print(table_logs.format(node_table))
            continue

        nodes = nodes.rdd.map(
            lambda x: create_node_dict(
                x["node_id"], node_name, x["_props"], node_schema, node_edges
            )
        )
        print(table_logs.format(node_table))

        with open(avro_filename, "a+b") as output_file:
            writer(output_file, parsed_schema, nodes.toLocalIterator())

    time_elapsed = datetime.now() - start_time
    print("Elapsed time: {}".format(time_elapsed))

    return avro_filename