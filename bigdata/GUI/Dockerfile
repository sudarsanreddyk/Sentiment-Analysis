FROM python:3.11-slim

WORKDIR /app

# copy the Streamlit script into the image
COPY streamlit_app.py .

# install required libs
RUN pip install --no-cache-dir \
        streamlit \
        requests \
        streamlit-autorefresh \
        pandas

# ───── JSON-array form of CMD must be on ONE line (or use \ at EOL) ─────
CMD ["streamlit", "run", "streamlit_app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
