# ZEIT -> Tandoor import HTTP service.
# Build context = contrib/zeit_import/:  docker build -t zeit-import contrib/zeit_import/
FROM python:3.12-alpine

# all HTTP in zeit_import goes through curl subprocesses
RUN apk add --no-cache curl \
    && adduser -D -H zeit

WORKDIR /app
COPY zeit_import.py serve.py ./

USER zeit
ENV PYTHONUNBUFFERED=1
EXPOSE 8199
CMD ["python3", "serve.py"]
