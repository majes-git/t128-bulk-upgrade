import logging
import sys

FORMAT = '%(asctime)s | %(levelname)-7s | %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

def set_debug():
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(format=FORMAT, level=logging.DEBUG, datefmt=DATE_FORMAT)

def format_msg(*msg):
    return ' '.join([str(s) for s in [*msg]])

def debug(*msg):
    # print('debug:', *msg)
    logging.debug(format_msg(*msg))

def error(*msg):
    logging.error(format_msg(*msg))
    sys.exit(1)

def info(*msg):
    logging.info(format_msg(*msg))

def warning(*msg):
    logging.warning(format_msg(*msg))


logging.basicConfig(format=FORMAT, level=logging.INFO, datefmt=DATE_FORMAT)
