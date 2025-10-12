import logging, sys
def setup(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Disable uvicorn access logs (HTTP request logs)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
