import logging
import os
import sys
import datetime
from logging.handlers import RotatingFileHandler
import pytz
from colorlog import ColoredFormatter  # Import ColoredFormatter

from config import ProjectConfig, PathsConfig

class GMT7Formatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        gmt7 = pytz.timezone('Asia/Ho_Chi_Minh')
        record_time = datetime.datetime.fromtimestamp(record.created, gmt7)
        if datefmt:
            return record_time.strftime(datefmt)
        else:
            return record_time.strftime(self.default_time_format)

def custom_logger(app_name="APP"):
    logs_dir = PathsConfig.LOG_DIR
    # Check if logs folder exists in project folder
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    # File formatter (no color)
    file_formatter = GMT7Formatter('[%(asctime)s][%(levelname)s][%(module)s] - %(message)s')

    # Console formatter with colors
    console_formatter = ColoredFormatter(
        '%(log_color)s[%(asctime)s][%(levelname)s][%(module)s] - %(message)s',
        datefmt=None,
        reset=True,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        },
        secondary_log_colors={},
        style='%'
    )

    # File handler
    log_file = os.path.join(logs_dir, f"{app_name}.log")
    logging_handler = RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    logging_handler.setLevel(logging.INFO)
    logging_handler.setFormatter(file_formatter)  # Use plain formatter for file

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)  # Use colored formatter for console

    logger = logging.getLogger(app_name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(logging_handler)  # Add file handler
    logger.addHandler(console_handler)  # Add console handler
    logger.propagate = False

    return logger

logger = custom_logger(app_name=ProjectConfig.PROJECT_NAME)
logger.info("Logger initialized")