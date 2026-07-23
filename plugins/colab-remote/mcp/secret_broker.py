"""Compatibility entry point for the packaged local secret broker."""

from colab_remote.secret_broker import *  # noqa: F403
from colab_remote.secret_broker import main


if __name__ == "__main__":
    raise SystemExit(main())
