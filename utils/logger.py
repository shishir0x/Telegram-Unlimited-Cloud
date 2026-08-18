import logging
from tqdm import tqdm


class TqdmLoggingHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            try:
                safe_msg = self.format(record).encode("utf-8", errors="replace").decode("utf-8")
                tqdm.write(safe_msg)
            except Exception:
                pass


class Logger:
    def __init__(self, name, level=logging.DEBUG):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")

        # Remove existing handlers to prevent duplicate logs
        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        # FileHandler for logging to a file
        file_handler = logging.FileHandler("logs.txt", mode="w", encoding="utf-8")
        file_handler.setFormatter(self.formatter)
        self.logger.addHandler(file_handler)

        # Custom TqdmLoggingHandler for console output
        stream_handler = TqdmLoggingHandler()
        stream_handler.setFormatter(self.formatter)
        self.logger.addHandler(stream_handler)

    def debug(self, message):
        self.logger.debug(message)

    def info(self, message):
        self.logger.info(message)

    def warning(self, message):
        self.logger.warning(message)

    def error(self, message):
        self.logger.error(message)

    def critical(self, message):
        self.logger.critical(message)
