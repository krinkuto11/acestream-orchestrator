import logging, sys
def setup(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Suppress verbose httpx logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
