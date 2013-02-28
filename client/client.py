#!/usr/bin/env python
"""This is the GRR client."""


import platform


from grr.client import conf

# pylint: disable=W0611
from grr.client import client_plugins
# pylint: enable=W0611

from grr.client import comms
from grr.lib import config_lib
from grr.lib import registry


class GRRClient(object):
  """A stand alone GRR client, which uses the HTTP mechanism."""

  stop = False

  def __init__(self):
    super(GRRClient, self).__init__()
    self.client = comms.GRRHTTPClient(
        ca_cert=config_lib.CONFIG["CA.certificate"],
        private_key=config_lib.CONFIG["Client.private_key"])

  def Run(self):
    """The client main loop - never exits."""
    # Generate the client forever.
    for _ in self.client.Run():
      pass


def main(unused_args):
  # Allow per platform configuration.
  config_lib.CONFIG.SetEnv("Environment.component",
                           "Client%s" % platform.system().title())
  registry.Init()

  client = GRRClient()
  client.Run()


if __name__ == "__main__":
  conf.StartMain(main)
