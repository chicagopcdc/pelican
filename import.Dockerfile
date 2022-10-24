FROM quay.io/cdis/python:python3.9-buster-stable

ENV appname=pelican

ENV DEBIAN_FRONTEND=noninteractive

#RUN mkdir -p /usr/share/man/man1
#RUN mkdir -p /usr/share/man/man7

RUN echo "pcdc log: installing apt packages"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    openjdk-11-jdk-headless \
    libssl1.1 \
    libgnutls30 \
    # dependency for pyscopg2
    libpq-dev \
    postgresql-client \
    wget \
    unzip \
    g++ \
    && rm -rf /var/lib/apt/lists/*

RUN echo "pcdc log: downloading hadoop"

ENV HADOOP_VERSION="3.2.1"
ENV HADOOP_HOME="/hadoop" \
    HADOOP_INSTALLATION_URL="http://archive.apache.org/dist/hadoop/common/hadoop-${HADOOP_VERSION}/hadoop-${HADOOP_VERSION}.tar.gz"

RUN wget -q ${HADOOP_INSTALLATION_URL} \
    && mkdir -p $HADOOP_HOME \
    && tar -xf hadoop-${HADOOP_VERSION}.tar.gz -C ${HADOOP_HOME} --strip-components 1 \
    && rm hadoop-${HADOOP_VERSION}.tar.gz \
    && rm -rf $HADOOP_HOME/share/doc

RUN echo "pcdc log: downloading sqoop"

ENV SQOOP_VERSION="1.4.7"
ENV SQOOP_HOME="/sqoop" \
    SQOOP_INSTALLATION_URL="http://archive.apache.org/dist/sqoop/${SQOOP_VERSION}/sqoop-${SQOOP_VERSION}.bin__hadoop-2.6.0.tar.gz" \
    SQOOP_MD5_URL="http://archive.apache.org/dist/sqoop/${SQOOP_VERSION}/sqoop-${SQOOP_VERSION}.bin__hadoop-2.6.0.tar.gz.md5"

RUN wget -q ${SQOOP_INSTALLATION_URL} \
    && wget -qO- ${SQOOP_MD5_URL} | md5sum -c - \
    && mkdir -p $SQOOP_HOME \
    && tar -xf sqoop-${SQOOP_VERSION}.bin__hadoop-2.6.0.tar.gz -C ${SQOOP_HOME} --strip-components 1 \
    && rm sqoop-${SQOOP_VERSION}.bin__hadoop-2.6.0.tar.gz \
    && rm -rf $SQOOP_HOME/docs

RUN echo "pcdc log: downloading postgresql jar"

ENV POSTGRES_JAR_VERSION="42.2.9"
ENV POSTGRES_JAR_URL="https://jdbc.postgresql.org/download/postgresql-${POSTGRES_JAR_VERSION}.jar" \
    POSTGRES_JAR_PATH=$SQOOP_HOME/lib/postgresql-${POSTGRES_JAR_VERSION}.jar \
    JAVA_HOME="/usr/lib/jvm/java-11-openjdk-amd64"

RUN wget -q ${POSTGRES_JAR_URL} -O ${POSTGRES_JAR_PATH}

RUN echo "pcdc log: setting home dir env vars and creating"

ENV HADOOP_CONF_DIR="$HADOOP_HOME/etc/hadoop" \
    HADOOP_MAPRED_HOME="${HADOOP_HOME}" \
    HADOOP_COMMON_HOME="${HADOOP_HOME}" \
    HADOOP_HDFS_HOME="${HADOOP_HOME}" \
    YARN_HOME="${HADOOP_HOME}" \
    ACCUMULO_HOME="/accumulo" \
    HIVE_HOME="/hive" \
    HBASE_HOME="/hbase" \
    HCAT_HOME="/hcatalog" \
    ZOOKEEPER_HOME="/zookeeper" \
    HADOOP_COMMON_LIB_NATIVE_DIR="${HADOOP_HOME}/lib/native" \
    LD_LIBRARY_PATH="${HADOOP_HOME}/lib/native:${LD_LIBRARY_PATH}"

RUN mkdir -p $ACCUMULO_HOME $HIVE_HOME $HBASE_HOME $HCAT_HOME $ZOOKEEPER_HOME

ENV PATH=${SQOOP_HOME}/bin:${HADOOP_HOME}/sbin:$HADOOP_HOME/bin:${JAVA_HOME}/bin:${PATH}

WORKDIR /pelican

RUN echo "pcdc log: installing pip"

RUN pip install --upgrade pip

RUN echo "pcdc log: installing poetry"

# install poetry
RUN curl -sSL https://install.python-poetry.org | python

COPY . /$appname
WORKDIR /$appname

# cache so that poetry install will run if these files change
COPY poetry.lock pyproject.toml /$appname/

RUN echo "pcdc log: installing poetry dependencies"

# install Indexd and dependencies via poetry

# old command:
# RUN . $HOME/.poetry/env \
#     && poetry config virtualenvs.create false \
#     && poetry install -vv --no-dev --no-interaction \
#     && poetry show -v

# ENV PYTHONUNBUFFERED=1

# RUN echo "pcdc log: executing entrypoint command (job_import.py)"

# ENTRYPOINT . $HOME/.poetry/env && poetry run python job_import.py

# new command:
ENV PATH="${PATH}:/root/.local/bin"
RUN poetry config virtualenvs.create false
RUN poetry install -vv --no-dev --no-interaction
RUN poetry show -v

ENV PYTHONUNBUFFERED=1

RUN echo "pcdc log: executing entrypoint command (job_import.py)"

ENTRYPOINT poetry run python job_import.py
