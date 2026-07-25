FROM python:3.11-slim
COPY . /app
WORKDIR /app
RUN pip install .
ENTRYPOINT ["modplusc"]
CMD ["run", "examples/hello.m2p"]
