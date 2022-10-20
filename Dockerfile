FROM python:3.9
RUN pip install kubernetes==v24.2.0
RUN pip install nats-py
RUN pip install asyncio-nats-streaming
COPY nautes-listener.py /opt/
WORKDIR /opt
