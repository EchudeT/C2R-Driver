FROM asterinas/dev:0.18.1-20260805
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nasm acpica-tools uuid-dev g++ make python3 gcc libc6-dev && rm -rf /var/lib/apt/lists/*
