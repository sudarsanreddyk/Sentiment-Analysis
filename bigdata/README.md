# BD25_Project_F8_A

Steps:
-----------------------------
1) docker compose up -d
Once container started running, producer sends messages automatically. 

To execute flink job
2) docker exec -it reddit-sentiment-jobmanager-1 flink run -py /opt/flink/usrlib/flink.py

To execute ML evaluation
3) docker compose exec ml_api python ml/sentiment_infer.py

The following services are accessible via browser:

| Service | Port | URL | Description |
|---------|------|-----|-------------|
| Kafka UI | 8080 | [http://localhost:8080](http://localhost:8080) | Monitor Kafka topics, consumer groups, and messages |
| Flink UI | 8081 | [http://localhost:8081](http://localhost:8081) | Manage and monitor Flink jobs and task managers |
| Streamlit UI | 8501 | [http://localhost:8501](http://localhost:8501) | Interactive dashboard for sentiment analysis results |
