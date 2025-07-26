import os
import uuid
import time
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.table import StreamTableEnvironment, EnvironmentSettings
from pyflink.table.udf import udf
from pyflink.table.expressions import col, call
from preprocessing import TextPreprocessor, SentimentScorer, NounExtractor, ensure_nltk_resources

# Set the correct NLTK data path
os.environ["NLTK_DATA"] = "/usr/local/share/nltk_data"

# Download necessary corpora
ensure_nltk_resources()

@udf(result_type="STRING")
def clean_text_udf(text: str):
    return TextPreprocessor().clean(text)

@udf(result_type="DOUBLE")
def sentiment_score_udf(text: str):
    return SentimentScorer().score(text)

@udf(result_type="STRING")
def extract_nouns_udf(text: str):
    nouns = NounExtractor().extract(text)
    return ", ".join(nouns)

def run_pipeline():
    """Run pipeline with a single sink"""
    kafka_servers = os.getenv("KAFKA_URL", "kafka") + ":9092"
    source_topic = "reddit_comments_raw"
    sink_topic = "reddit_comments_processed"

    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(1)
    
    t_env = StreamTableEnvironment.create(env, environment_settings=EnvironmentSettings.in_streaming_mode())
    
    # Set restart strategy using table environment configuration
    t_env.get_config().get_configuration().set_string("restart-strategy", "fixed-delay")
    t_env.get_config().get_configuration().set_string("restart-strategy.fixed-delay.attempts", "3")
    t_env.get_config().get_configuration().set_string("restart-strategy.fixed-delay.delay", "30s")

    # Register UDFs
    t_env.create_temporary_function("clean_text", clean_text_udf)
    t_env.create_temporary_function("score_sentiment", sentiment_score_udf)
    t_env.create_temporary_function("extract_nouns", extract_nouns_udf)

    # Source table
    t_env.execute_sql(f"""
        CREATE TABLE reddit_comments (
            id STRING,
            author STRING,
            created_utc BIGINT,
            body STRING,
            score BIGINT,
            subreddit STRING,
            controversiality BOOLEAN
        ) WITH (
            'connector' = 'kafka',
            'topic' = '{source_topic}',
            'properties.bootstrap.servers' = '{kafka_servers}',
            'properties.group.id' = 'flink-preprocessing-single',
            'scan.startup.mode' = 'earliest-offset',
            'format' = 'json'
        )
    """)

    # Create single sink table
    unique_run_id = str(uuid.uuid4())[:8]
    job_timestamp = int(time.time())
    
    t_env.execute_sql(f"""
        CREATE TABLE reddit_processed (
            id STRING,
            author STRING,
            created_utc BIGINT,
            score BIGINT,
            subreddit STRING,
            controversiality BOOLEAN,
            cleaned STRING,
            sentiment DOUBLE,
            nouns STRING,
            PRIMARY KEY (id) NOT ENFORCED
        ) WITH (
            'connector' = 'upsert-kafka',
            'topic' = '{sink_topic}',
            'properties.bootstrap.servers' = '{kafka_servers}',
            'properties.transactional.id.prefix' = 'reddit-single-{job_timestamp}-{unique_run_id}',
            'sink.parallelism' = '1',
            'key.format' = 'json',
            'value.format' = 'json'
        )
    """)

    # Processing pipeline
    print("Starting processing pipeline...")
    
    source = t_env.from_path("reddit_comments")
    enriched = source \
        .filter((col("body") != '[deleted]') & (col("body") != '[removed]')) \
        .select(
            col("id"), 
            col("author"), 
            col("created_utc"), 
            col("score"),
            col("subreddit"), 
            col("controversiality"),
            call("clean_text", col("body")).alias("cleaned"),
            call("score_sentiment", call("clean_text", col("body"))).alias("sentiment"),
            call("extract_nouns", call("clean_text", col("body"))).alias("nouns")
        )

    # Execute the pipeline
    result = enriched.execute_insert("reddit_processed")
    
    print("Pipeline started successfully! Processing all data to single sink...")
    result.wait()
    print("Processing completed!")

if __name__ == "__main__":
    run_pipeline()