"""Container healthcheck for the worker: exit 0 when this host's worker has a fresh heartbeat."""

import sys

from app.db import get_redis
from app.health import local_worker_alive


def main() -> int:
    return 0 if local_worker_alive(get_redis()) else 1


if __name__ == "__main__":
    sys.exit(main())
