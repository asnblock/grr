#!/usr/bin/env python
"""Web authentication classes for the GUI."""

import collections


from django import http
import logging

from grr.lib import access_control
from grr.lib import config_lib
from grr.lib import log
from grr.lib import registry


config_lib.DEFINE_string(
    "AdminUI.webauth_manager", "NullWebAuthManager",
    "The web auth manager for controlling access to the UI.")


class BaseWebAuthManager(object):
  """A class managing web authentication.

  This class is responsible for deciding if the user will have access to the web
  interface and for generating the token that will be passed to the functions
  that deal with data.

  Checks are done using a decorator function.
  """

  __metaclass__ = registry.MetaclassRegistry

  def SecurityCheck(self, func, request, *args, **kwargs):
    """A decorator applied to protected web handlers.

    Args:
      func: The wrapped function to call.
      request: The web request.

    Returns:
      A django http response object.

    This will get called for all requests that get passed through one of our
    handlers that is wrapped in @SecurityCheck.
    """

  def RedirectBase(self):
    """Return a redirect to the main GRR page."""
    return http.HttpResponsePermanentRedirect(config_lib.CONFIG["AdminUI.url"])


class BasicWebAuthManager(BaseWebAuthManager):
  """Manager using basic auth using the config file."""

  def __init__(self):
    """Constructor."""
    # Reuse the basic ACL manager functions for accessing the config.
    self._aclmanager = access_control.BasicAccessControlManager()
    super(BasicWebAuthManager, self).__init__()

  def SecurityCheck(self, func, request, *args, **kwargs):
    """Wrapping function."""
    event_id = log.LOGGER.GetNewEventId()

    # Modify request adding an event_id attribute to track the event
    request.event_id = event_id
    request.user = ""

    authorized = False
    try:
      auth_type, authorization = request.META.get(
          "HTTP_AUTHORIZATION", " ").split(" ", 1)
      if auth_type == "Basic":
        user, password = authorization.decode("base64").split(":", 1)
        # Check the hash is ok
        auth_obj = collections.namedtuple("AuthObj", "user_provided_hash")
        auth_obj.user_provided_hash = password
        if self._aclmanager.user_manager.CheckUserAuth(user, auth_obj):
          authorized = True
          # The password is ok - update the user
          request.user = user

    except (IndexError, KeyError):
      pass

    if not authorized:
      result = http.HttpResponse("Unauthorized", status=401)
      result["WWW-Authenticate"] = "Basic realm='Secure Area'"
      return result

    # Modify this to implement additional checking (e.g. enforce SSL).
    response = func(request, *args, **kwargs)
    return response


class NullWebAuthManager(BaseWebAuthManager):
  """Null web auth manager always returns test user unless set."""

  def __init__(self, *args, **kwargs):
    super(NullWebAuthManager, self).__init__(*args, **kwargs)
    self.username = "test"

  def SetUserName(self, username):
    self.username = username

  def SecurityCheck(self, func, request, *args, **kwargs):
    """A decorator applied to protected web handlers."""
    request.event_id = "1"
    request.user = self.username
    request.token = access_control.ACLToken("Testing", "Just a test")
    return func(request, *args, **kwargs)


# Global to store the configured web auth manager.
WEBAUTH_MANAGER = None


def SecurityCheck(func):
  """A decorator applied to protected web handlers."""

  def Wrapper(request, *args, **kwargs):
    """Wrapping function."""
    if WEBAUTH_MANAGER is None:
      raise RuntimeError("Attempt to initialize before WEBAUTH_MANAGER set.")
    return WEBAUTH_MANAGER.SecurityCheck(func, request, *args, **kwargs)

  return Wrapper


class WebAuthInit(registry.InitHook):
  pre = ["GuiPluginsInit"]

  def RunOnce(self):
    """Run this once on init."""
    global WEBAUTH_MANAGER  # pylint: disable=global-statement

    # pylint: disable=g-bad-name
    WEBAUTH_MANAGER = BaseWebAuthManager.NewPlugin(
        config_lib.CONFIG["AdminUI.webauth_manager"])()

    # pylint: enable=g-bad-name
    logging.info("Using webauth manager %s", WEBAUTH_MANAGER)
