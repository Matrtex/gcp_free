from __future__ import annotations

import traceback

from gcp_utils import *
from gcp_operations import *
from gcp_instance import *
from gcp_reroll import *
from gcp_firewall import *
from gcp_remote import *
from gcp_menu import *
from gcp_cli import *
from gcp_logging import get_logger

LOGGER = get_logger()


if __name__ == "__main__":
    try:
        configure_stdio()
        args = parse_args()
        if not run_cli(args):
            main()
    except KeyboardInterrupt:
        print("\n[用户终止] 脚本已停止。")
    except Exception as e:
        print_error(f"发生异常: {summarize_exception(e)}")
        LOGGER.error(traceback.format_exc())
