#!/usr/bin/env python
# Copyright 2012 Google Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Some multiclient flows aka hunts."""



import re
import stat

import logging

from grr.lib import access_control
from grr.lib import aff4
from grr.lib import data_store
from grr.lib import flow
from grr.lib import rdfvalue
from grr.lib import type_info
from grr.lib import utils
from grr.lib.aff4_objects import aff4_grr
from grr.lib.hunts import implementation


class CreateAndRunGenericHuntFlow(flow.GRRFlow):
  """Create and run GenericHunt with given name, args and rules."""
  # This flow can run on any client without ACL enforcement (an SUID flow).
  ACL_ENFORCED = False

  # TODO(user): describe proper types for hunt_flow_args and hunt_rules
  flow_typeinfo = type_info.TypeDescriptorSet(
      type_info.String(
          description="Hunt id.",
          name="hunt_id"),

      type_info.String(
          description="Hunt flow name.",
          name="hunt_flow_name"),

      type_info.Any(
          description="A dictionary of hunt flow's arguments.",
          name="hunt_flow_args"),

      type_info.Any(
          description="Foreman rules for the hunt.",
          name="hunt_rules"),

      type_info.Integer(
          description="Expiration time for this hunt in seconds.",
          default=None,
          name="expiry_time"),

      type_info.Integer(
          description="A client limit.",
          default=None,
          name="client_limit"),
      )

  @flow.StateHandler()
  def Start(self):
    """Create the hunt, perform permissions check and run it."""
    hunt = GenericHunt(flow_name=self.hunt_flow_name,
                       args=self.hunt_flow_args,
                       expiry_time=self.expiry_time,
                       client_limit=self.client_limit,
                       token=self.token)

    hunt.AddRule(rules=self.hunt_rules)
    hunt.WriteToDataStore()

    # We have to create special token here, because within the flow
    # token has supervisor access.
    check_token = access_control.ACLToken(username=self.token.username,
                                          reason=self.token.reason)
    data_store.DB.security_manager.CheckAccess(
        check_token, [aff4.ROOT_URN.Add("hunts").Add(hunt.session_id)], "x")
    hunt.Run()


class RunHuntFlow(flow.GRRFlow):
  """Run already created hunt with given id."""
  # This flow can run on any client without ACL enforcement (an SUID flow).
  ACL_ENFORCED = False

  flow_typeinfo = type_info.TypeDescriptorSet(
      type_info.RDFURNType(
          description="The URN of the hunt to execute.",
          name="hunt_urn"),
      )

  @flow.StateHandler()
  def Start(self):
    """Find a hunt, perform a permissions check and run it."""
    aff4_hunt = aff4.FACTORY.Open(self.hunt_urn, age=aff4.ALL_TIMES,
                                  token=self.token)
    hunt = aff4_hunt.GetFlowObj()

    # We have to create special token here, because within the flow
    # token has supervisor access.
    check_token = access_control.ACLToken(username=self.token.username,
                                          reason=self.token.reason)
    data_store.DB.security_manager.CheckAccess(
        check_token, [aff4.ROOT_URN.Add("hunts").Add(hunt.session_id)], "x")

    # Make the hunt token a supervisor so it can be started.
    hunt.token.supervisor = True
    hunt.Run()


class CheckHuntAccessFlow(flow.GRRFlow):
  # This flow can run on any client without ACL enforcement (an SUID flow).
  ACL_ENFORCED = False

  flow_typeinfo = type_info.TypeDescriptorSet(
      type_info.String(
          description="Hunt id.",
          name="hunt_id"),
      )

  @flow.StateHandler()
  def Start(self):
    if not self.hunt_id:
      raise RuntimeError("hunt_id was not provided.")

    check_token = access_control.ACLToken(username=self.token.username,
                                          reason=self.token.reason,
                                          requested_access="x")
    data_store.DB.security_manager.CheckAccess(
        check_token, [aff4.ROOT_URN.Add("hunts").Add(self.hunt_id)], "x")


class SampleHunt(implementation.GRRHunt):
  """This hunt just looks for the presence of a evil.txt in /tmp.

  Scheduling the hunt works like this:

  > hunt = hunts.SampleHunt()

  # We want to schedule on clients that run windows and OS_RELEASE 7.
  > int_rule = rdfvalue.ForemanAttributeInteger(
                   attribute_name=client.Schema.OS_RELEASE.name,
                   operator=rdfvalue.ForemanAttributeInteger.Enum("EQUAL"),
                   value=7)
  > regex_rule = hunts.GRRHunt.MATCH_WINDOWS

  # Run the hunt when both those rules match.
  > hunt.AddRule([int_rule, regex_rule])

  # Now we can test how many clients in the database match the rules.
  # Warning, this might take some time since it looks at all the stored clients.
  > hunt.TestRules()

  Out of 3171 checked clients, 2918 matched the given rule set.

  # This looks good, we exclude the few Linux / Mac clients in the datastore.

  # Now we can start the hunt. Note that this hunt is actually designed for
  # Linux / Mac clients so the example rules should not be used for this hunt.
  > hunt.Run()

  """

  flow_typeinfo = type_info.TypeDescriptorSet(
      type_info.String(
          description="evil filename to search for.",
          name="filename",
          default="/tmp/evil.txt")
      )

  @flow.StateHandler(next_state=["StoreResults"])
  def Start(self, responses):
    client_id = responses.request.client_id
    pathspec = rdfvalue.RDFPathSpec(pathtype=rdfvalue.RDFPathSpec.Enum("OS"),
                                    path=self.filename)

    self.CallFlow("GetFile", pathspec=pathspec, next_state="StoreResults",
                  client_id=client_id)

  @flow.StateHandler()
  def StoreResults(self, responses):
    """Stores the responses."""
    client_id = responses.request.client_id

    if not responses.success:
      logging.info("Client %s has no file %s.", client_id, self.filename)
    else:
      logging.info("Client %s has a file %s.", client_id, self.filename)
      self.MarkClientBad(client_id)

    self.MarkClientDone(client_id)


class RegistryFileHunt(implementation.GRRHunt):
  """A hunt that downloads registry files."""

  registry_files = ["DEFAULT", "SAM", "SECURITY", "SOFTWARE", "SYSTEM"]

  files = None

  @flow.StateHandler(next_state=["StoreResults"])
  def Start(self, responses):
    """Start."""
    client_id = responses.request.client_id

    if not self.files:
      self.files = {}

    self.files[client_id] = 0
    for filename in self.registry_files:
      pathspec = rdfvalue.RDFPathSpec(
          pathtype=rdfvalue.RDFPathSpec.Enum("TSK"),
          path=r"C:\windows\system32\config\%s" % filename)

      self.files[client_id] += 1
      self.CallFlow("GetFile", pathspec=pathspec, next_state="StoreResults",
                    client_id=client_id)

    client = aff4.FACTORY.Open(aff4.ROOT_URN.Add(client_id), mode="r",
                               token=self.token)
    users = client.Get(client.Schema.USER) or []
    for user in users:
      pathspec = rdfvalue.RDFPathSpec(
          pathtype=rdfvalue.RDFPathSpec.Enum("TSK"),
          path=user.homedir + r"\NTUSER.DAT")
      self.files[client_id] += 1
      self.CallFlow("GetFile", pathspec=pathspec, next_state="StoreResults",
                    client_id=client_id)

  @flow.StateHandler()
  def StoreResults(self, responses):
    """Stores the responses."""
    client_id = responses.request.client_id
    if responses.success:
      pathspec = responses.First().pathspec
      self.LogResult(
          client_id, "Got file %s." % pathspec,
          aff4.AFF4Object.VFSGRRClient.PathspecToURN(pathspec, client_id))
    else:
      self.LogClientError(client_id, log_message=responses.status)

    self.files[client_id] -= 1
    if self.files[client_id] == 0:
      self.MarkClientDone(client_id)


class ProcessesHunt(implementation.GRRHunt):
  """A hunt that downloads process lists."""

  @flow.StateHandler(next_state=["StoreResults"])
  def Start(self, responses):
    """Start."""
    client_id = responses.request.client_id

    self.CallFlow("ListProcesses", next_state="StoreResults",
                  client_id=client_id)

  @flow.StateHandler()
  def StoreResults(self, responses):
    """Stores the responses."""
    client_id = responses.request.client_id
    if responses.success:
      self.LogResult(client_id, "Got process listing.",
                     aff4.ROOT_URN.Add(client_id).Add("processes"))
    else:
      self.LogClientError(client_id, log_message=responses.status)

    self.MarkClientDone(client_id)

  def FindProcess(self, process_name):
    """This finds processes that contain process_name."""

    hunt = aff4.FACTORY.Open(self.urn,
                             age=aff4.ALL_TIMES, token=self.token)
    log = hunt.GetValuesForAttribute(hunt.Schema.LOG)

    for log_entry in log:
      proc_list = aff4.FACTORY.Open(log_entry.urn, "ProcessListing",
                                    token=self.token)
      procs = proc_list.Get(proc_list.Schema.PROCESSES)
      for process in procs:
        if process_name.lower() in process.name.lower():
          print "Found process for %s:" % log_entry.client_id
          print process

  def ProcessHistogram(self, full_path=True):
    """This generates a histogram of all the processes found."""

    hist = {}

    hunt = aff4.FACTORY.Open(self.urn,
                             age=aff4.ALL_TIMES, token=self.token)
    log = hunt.GetValuesForAttribute(hunt.Schema.LOG)

    for log_entry in log:
      proc_list = aff4.FACTORY.Open(log_entry.urn, "ProcessListing",
                                    token=self.token)
      procs = proc_list.Get(proc_list.Schema.PROCESSES)
      for process in procs:
        if full_path:
          cmd = " ".join(process.cmdline)
        else:
          cmd = process.name
        hist.setdefault(cmd, 0)
        hist[cmd] += 1

    proc_list = sorted(hist.iteritems(), reverse=True, key=lambda (k, v): v)
    for proc, freq in proc_list:
      print "%d  %s" % (freq, proc)

    return hist


class MBRHunt(implementation.GRRHunt):
  """A hunt that downloads MBRs."""

  def __init__(self, length=4096, **kw):
    self.length = length
    super(MBRHunt, self).__init__(**kw)

  @flow.StateHandler(next_state=["StoreResults"])
  def Start(self, responses):
    """Start."""
    client_id = responses.request.client_id

    self.CallFlow("GetMBR", length=self.length, next_state="StoreResults",
                  client_id=client_id)

  @flow.StateHandler()
  def StoreResults(self, responses):
    """Stores the responses."""
    client_id = responses.request.client_id
    if responses.success:
      self.LogResult(client_id, "Got MBR.",
                     aff4.ROOT_URN.Add(client_id).Add("mbr"))
    else:
      self.LogClientError(client_id, log_message=utils.SmartStr(
          responses.status))

    self.MarkClientDone(client_id)

  def MBRHistogram(self, length=512):
    """Prints a histogram of the MBRs found."""

    hist = {}

    hunt = aff4.FACTORY.Open(self.urn,
                             age=aff4.ALL_TIMES, token=self.token)

    log = hunt.GetValuesForAttribute(hunt.Schema.LOG)

    for log_entry in log:
      try:
        mbr = aff4.FACTORY.Open(log_entry.urn, token=self.token)
        mbr_data = mbr.Read(length)
        # Skip over the table of primary partitions.
        mbr_data = mbr_data[:440] + "\x00"*70 + mbr_data[440+70:]
        key = mbr_data.encode("hex")
        hist.setdefault(key, []).append(log_entry.client_id)
      except AttributeError:
        print "Error for urn %s" % log_entry.urn

    mbr_list = sorted(hist.iteritems(), reverse=True, key=lambda (k, v): len(v))
    for mbr, freq in mbr_list:
      print "%d  %s" % (len(freq), mbr)

    return mbr_list


class MatchRegistryHunt(implementation.GRRHunt):
  """A hunt to download registry keys containing a search string."""

  def __init__(self, paths, search_string=None, max_depth=None,
               match_case=False, **kw):
    """This hunt looks for registry keys matching a given string.

    Args:
      paths: A list of registry keys where this hunt should look for values.
      search_string: The string to look for. If none is given, this just
                     downloads all the values found and searches can be done
                     later using FindString().
      max_depth: If given, the hunt will be restricted to a maximal path depth.
      match_case: The match has to be case sensitive.
    """

    self.paths = paths
    self.max_depth = max_depth
    self.match_case = match_case
    self.search_string = search_string
    if not self.match_case:
      self.search_string = search_string.lower()

    super(MatchRegistryHunt, self).__init__(**kw)

  @flow.StateHandler()
  def Start(self, responses):
    """Start."""
    client_id = responses.request.client_id

    for path in self.paths:
      request = rdfvalue.RDFFindSpec()
      request.pathspec.path = path
      request.pathspec.pathtype = rdfvalue.RDFPathSpec.Enum("REGISTRY")

      if self.max_depth:
        request.max_depth = self.max_depth

      # Hard coded limit so this does not get too big.
      request.iterator.number = 10000
      self.CallClient("Find", request, client_id=client_id,
                      next_state="StoreResults")

  def Match(self, s):
    if not self.match_case:
      s = s.lower()
    return self.search_string in s

  @flow.StateHandler()
  def StoreResults(self, responses):
    """Stores the responses."""
    client_id = responses.request.client_id
    if responses.success:
      for response in responses:
        pathspec = response.hit.pathspec
        if stat.S_ISDIR(response.hit.st_mode):
          continue
        vfs_urn = aff4.AFF4Object.VFSGRRClient.PathspecToURN(
            pathspec, client_id)
        data = utils.SmartStr(response.hit.registry_data.GetValue())
        fd = aff4.FACTORY.Create(vfs_urn, "VFSFile", mode="w", token=self.token)
        fd.Set(fd.Schema.STAT(response.hit))
        fd.Set(fd.Schema.PATHSPEC(response.hit.pathspec))
        fd.Close(sync=False)
        if not self.search_string:
          self.LogResult(client_id, "Registry key downloaded.", vfs_urn)
        else:
          if self.Match(data):
            self.LogResult(client_id, "Matching registry key.", vfs_urn)
            self.MarkClientBad(client_id)
          else:
            self.LogResult(client_id, "Registry key not matched.", vfs_urn)
    else:
      self.LogClientError(client_id, log_message=utils.SmartStr(
          responses.status))

    self.MarkClientDone(client_id)

  def FindString(self, search_term, match_case=True):
    """Finds a string in the downloaded registry keys."""

    hunt = aff4.FACTORY.Open("aff4:/hunts/%s" % self.session_id,
                             age=aff4.ALL_TIMES, token=self.token)

    log = hunt.GetValuesForAttribute(hunt.Schema.LOG)

    urns = []
    for log_entry in log:
      urns.append(log_entry.urn)

    if not match_case:
      search_term = search_term.lower()

    for key in aff4.FACTORY.MultiOpen(urns, token=self.token):
      value = utils.SmartStr(key.Get(key.Schema.STAT).registry_data.GetValue())
      if not match_case:
        value = value.lower()
      if search_term in value:
        print "Match: %s: %s" % (key, value)


class RunKeysHunt(implementation.GRRHunt):
  """A hunt for the RunKey collection."""

  @flow.StateHandler(next_state="StoreResults")
  def Start(self, responses):
    client_id = responses.request.client_id
    self.CallFlow("CollectRunKeys", next_state="StoreResults",
                  client_id=client_id)

  @flow.StateHandler()
  def StoreResults(self, responses):
    """Stores the responses."""
    client_id = responses.request.client_id
    if responses.success:
      self.LogResult(client_id, "Downloaded RunKeys",
                     aff4.ROOT_URN.Add(client_id).Add("analysis/RunKeys"))
    else:
      self.LogClientError(client_id, log_message=utils.SmartStr(
          responses.status))

    self.MarkClientDone(client_id)

  def Histogram(self):
    """Creates a histogram of all the filenames found in the RunKeys."""

    hist = {}

    hunt = aff4.FACTORY.Open("aff4:/hunts/%s" % self.session_id,
                             age=aff4.ALL_TIMES, token=self.token)

    log = hunt.GetValuesForAttribute(hunt.Schema.LOG)

    client_ids = [l.client_id for l in log]

    to_read = []

    while client_ids:
      clients = aff4.FACTORY.MultiOpen(
          ["aff4:/%s" % client_id for client_id in client_ids[:1000]])
      client_ids = client_ids[1000:]

      for client in clients:
        for user in client.Get(client.Schema.USER):
          to_read.append("aff4:/%s/analysis/RunKeys/%s/RunOnce" %
                         (client.client_id, user.username))
          to_read.append("aff4:/%s/analysis/RunKeys/%s/Run" %
                         (client.client_id, user.username))
        to_read.append("aff4:/%s/analysis/RunKeys/System/RunOnce" %
                       client.client_id)
        to_read.append("aff4:/%s/analysis/RunKeys/System/Run" %
                       client.client_id)

    print "Processing %d collections." % len(to_read)
    collections_done = 0

    while to_read:
      # Only do 1000 at a time.
      collections_done += len(to_read[:1000])
      collections = aff4.FACTORY.MultiOpen(to_read[:1000], token=self.token)
      to_read = to_read[1000:]

      for collection in collections:
        try:
          for runkey in collection:
            key = runkey.filepath.replace("\"", "")
            key = re.sub(r"Users\\[^\\]+\\", r"Users\\USER\\", key)
            hist.setdefault(key, set()).add(str(collection.urn)[6:6+18])
        except AttributeError:
          pass

      print "%d collections done." % collections_done

    rk_list = sorted(hist.iteritems(), reverse=True, key=lambda (k, v): len(v))
    for rk, freq in rk_list:
      print "%d  %s" % (len(freq), rk)

    return rk_list


class FetchFilesHunt(implementation.GRRHunt):
  """This hunt launches the FetchAllFiles flow on a subset of hosts.

  Scheduling the hunt works like this:

  > hunt = hunts.FetchFilesHunt(client_limit=20)

  # We want to schedule on clients that run windows and OS_RELEASE 7.
  #
  # For now, we also want to limit ourselves to Windows *servers*.
  # TODO(user): This needs to be expressed here.

  > int_rule = rdfvalue.ForemanAttributeInteger(
                   attribute_name=client.Schema.OS_RELEASE.name,
                   operator=rdfvalue.ForemanAttributeInteger.Enum("EQUAL"),
                   value=7)
  > regex_rule = hunts.GRRHunt.MATCH_WINDOWS

  # Run the hunt when both those rules match.
  > hunt.AddRule([int_rule, regex_rule])

  # Now we can test how many clients in the database match the rules.
  # Warning, this might take some time since it looks at all the stored clients.
  > hunt.TestRules()

  Out of 3171 checked clients, 2918 matched the given rule set.

  > hunt.Run()

  """

  def __init__(self, **kw):
    super(FetchFilesHunt, self).__init__(**kw)

  @flow.StateHandler(next_state=["MarkDone"])
  def Start(self, responses):
    client_id = responses.request.client_id
    self.CallFlow("FetchAllFiles", next_state="MarkDone",
                  client_id=client_id)

  @flow.StateHandler()
  def MarkDone(self, responses):
    """Mark a client as done."""
    client_id = responses.request.client_id
    self.MarkClientDone(client_id)


class MemoryHunt(implementation.GRRHunt):
  """This is a hunt to find signatures in memory."""

  flow_typeinfo = type_info.TypeDescriptorSet(
      type_info.GrepspecType(
          description="Search memory for this expression.",
          name="request"),

      type_info.String(
          description="A path relative to the client to put the output.",
          name="output",
          default="analysis/find/{u}-{t}"),
      )

  @flow.StateHandler(next_state=["SavePlatform"])
  def Start(self, responses):
    client_id = responses.request.client_id
    self.CallClient("GetPlatformInfo", client_id=client_id,
                    next_state="SavePlatform")

  @flow.StateHandler(next_state=["Grep"])
  def SavePlatform(self, responses):
    """Save the updated client information."""
    client_id = responses.request.client_id
    if responses.success:
      response = responses.First()
      client = aff4.FACTORY.Open("aff4:/%s" % client_id, mode="rw")
      client.Set(client.Schema.HOSTNAME(response.node))
      client.Set(client.Schema.SYSTEM(response.system))
      client.Set(client.Schema.OS_RELEASE(response.release))
      client.Set(client.Schema.OS_VERSION(response.version))
      client.Set(client.Schema.UNAME("%s-%s-%s" % (
          response.system, response.version,
          response.release)))
      client.Close()

    self.CallFlow("LoadMemoryDriver", next_state="Grep",
                  client_id=client_id)

  @flow.StateHandler(next_state=["Unload"])
  def Grep(self, responses):
    client_id = responses.request.client_id
    if not responses.success:
      self.LogClientError(client_id, log_message=utils.SmartStr(
          responses.status))
      return

    self.CallFlow("Grep", output=self.output,
                  request=self.request, client_id=client_id,
                  next_state="Unload")

  @flow.StateHandler(next_state=["MarkDone"])
  def Unload(self, responses):
    """Saves the log and unloads the driver."""
    client_id = responses.request.client_id
    if responses.success:
      self.LogResult(client_id, "Memory grep completed, found %d hits." %
                     len(responses),
                     aff4.ROOT_URN.Add(client_id).Add(self.output))
    else:
      self.LogClientError(client_id, log_message=utils.SmartStr(
          responses.status))

    self.CallFlow("UnloadMemoryDriver", next_state="MarkDone",
                  client_id=client_id)

  @flow.StateHandler()
  def MarkDone(self, responses):
    """Mark a client as done."""
    client_id = responses.request.client_id
    self.MarkClientDone(client_id)


class GenericHunt(implementation.GRRHunt):
  """This is a hunt to start any flow on multiple clients.

  Args:
    flow_name: The flow to run.
    args: A dict containing the parameters for the flow.
  """

  flow_typeinfo = type_info.TypeDescriptorSet(
      type_info.String(
          description="Name of flow to run.",
          name="flow_name",
          default=""),
      type_info.GenericProtoDictType(
          name="args",
          description="Parameters passed to the child flow.",
          ),
      type_info.Bool(
          name="collect_replies",
          description="Collect hunt's replies into collection.",
          default=False,
          ),
      )

  def __init__(self, **kw):
    super(GenericHunt, self).__init__(**kw)

    # Our results will be written inside this collection.
    self.collection = aff4.FACTORY.Create(
        self.urn.Add("Results"), "RDFValueCollection",
        token=self.token)

  def Run(self, description=None):
    if description is None:
      desc = []
      for k, v in sorted(self.args.ToDict().items()):
        desc.append("%s: %s" % (utils.SmartStr(k), utils.SmartStr(v)))
      description = "%s with args { %s }." % (
          self.flow_name, ",".join(desc))
    super(GenericHunt, self).Run(description=description)

  @flow.StateHandler(next_state=["MarkDone"])
  def Start(self, responses):
    client_id = responses.request.client_id

    args = self.args.ToDict()
    if not self.collect_replies:
      args["send_replies"] = False

    self.CallFlow(self.flow_name, next_state="MarkDone", client_id=client_id,
                  **args)

  def Save(self):
    if self.collect_replies:
      with self.lock:
        # Flush results frequently so users can monitor them as they come in.
        self.collection.Flush()

    super(GenericHunt, self).Save()

  @flow.StateHandler()
  def MarkDone(self, responses):
    """Mark a client as done."""
    client_id = responses.request.client_id

    if responses.success:
      self.LogResult(client_id, "Flow %s completed." % self.flow_name)

      if self.collect_replies:
        with self.lock:
          for response in responses:
            msg = rdfvalue.GRRMessage(payload=response)
            msg.source = client_id
            self.collection.Add(msg)
    else:
      self.LogClientError(client_id, log_message=utils.SmartStr(
          responses.status))

    self.MarkClientDone(client_id)

  def Stop(self):
    super(GenericHunt, self).Stop()


class VariableGenericHunt(implementation.GRRHunt):
  """A generic hunt using different flows for each client.

  Args:
    flows: A dictionary where the keys are the client_ids to start flows on and
           the values are lists of pairs (flow_name, dict of args) similar to
           the generic hunt above.
  """

  flow_typeinfo = type_info.TypeDescriptorSet(
      type_info.GenericProtoDictType(
          name="flows",
          description=("A dictionary where the keys are the client_ids to start"
                       " flows on and the values are lists of pairs (flow_name,"
                       " dict of args)"),
          ),
      type_info.Bool(
          name="collect_replies",
          description="Collect hunt's replies into collection.",
          default=False,
          ),
      )

  def __init__(self, **kw):
    super(VariableGenericHunt, self).__init__(**kw)

    client_id_re = aff4_grr.VFSGRRClient.CLIENT_ID_RE
    for client_id in self.flows:
      if not client_id_re.match(client_id):
        raise RuntimeError("%s is not a valid client_id." % client_id)

    # Our results will be written inside this collection.
    self.collection = aff4.FACTORY.Create(
        self.urn.Add("Results"), "RDFValueCollection",
        token=self.token)

  def Save(self):
    if self.collect_replies:
      with self.lock:
        # Flush results frequently so users can monitor them as they come in.
        self.collection.Flush()

    super(VariableGenericHunt, self).Save()

  @flow.StateHandler(next_state=["MarkDone"])
  def Start(self, responses):
    client_id = responses.request.client_id

    try:
      flow_list = self.flows[client_id]
    except KeyError:
      self.LogClientError(client_id, "No flow found for client %s." % client_id)
      self.MarkClientDone(client_id)
      return
    for flow_name, args in flow_list:
      self.CallFlow(flow_name, next_state="MarkDone", client_id=client_id,
                    **args)

  @flow.StateHandler()
  def MarkDone(self, responses):
    """Mark a client as done."""
    client_id = responses.request.client_id
    if not responses.success:
      self.LogClientError(client_id, log_message=utils.SmartStr(
          responses.status))
    else:
      msg = "Flow %s completed." % responses.request.flow_name
      self.LogResult(client_id, msg)

      if self.collect_replies:
        with self.lock:
          for response in responses:
            msg = rdfvalue.GRRMessage(payload=response)
            msg.source = client_id
            self.collection.Add(msg)

    # This is not entirely accurate since it will mark the client as done as
    # soon as the first flow is done.
    self.MarkClientDone(client_id)

  def ManuallyScheduleClients(self):
    """Schedule all flows without using the Foreman.

    Since we know all the client ids to run on we might as well just schedule
    all the flows and wait for the results.
    """

    for client_id in self.flows:
      self.StartClient(self.session_id, client_id, self.client_limit)


class CollectFilesHunt(implementation.GRRHunt):
  """A hunt to collect files from various clients.

  Args:
    files_by_client:
      A dictionary where the keys are the client_ids to collect files from and
      the values are lists of Pathspecs to get from this client.
  """

  def __init__(self, files_by_client, **kw):

    client_id_re = aff4_grr.VFSGRRClient.CLIENT_ID_RE
    for client_id in files_by_client:
      if not client_id_re.match(client_id):
        raise RuntimeError("%s is not a valid client_id." % client_id)

    self.files_by_client = files_by_client
    super(CollectFilesHunt, self).__init__(**kw)

  @flow.StateHandler(next_state=["MarkDone"])
  def Start(self, responses):
    """Start."""
    client_id = responses.request.client_id

    try:
      file_list = self.files_by_client[client_id]
    except KeyError:
      self.LogClientError(client_id,
                          "No files found for client %s." % client_id)
      self.MarkClientDone(client_id)
      return
    for pathspec in file_list:
      self.CallFlow("GetFile", next_state="MarkDone", client_id=client_id,
                    pathspec=pathspec)

  @flow.StateHandler()
  def MarkDone(self, responses):
    """Mark a client as done."""
    client_id = responses.request.client_id
    if not responses.success:
      self.LogClientError(client_id, log_message=utils.SmartStr(
          responses.status))
    else:
      for response in responses:
        msg = "Got file %s (%s)." % (response.aff4path, client_id)
        self.LogResult(client_id, msg, urn=response.aff4path)

    # This is not entirely accurate since it will mark the client as done as
    # soon as the first flow is done.
    self.MarkClientDone(client_id)

  def ManuallyScheduleClients(self):
    """Schedule all flows without using the Foreman.

    Since we know all the client ids to run on we might as well just schedule
    all the flows and wait for the results.
    """

    for client_id in self.files_by_client:
      self.StartClient(self.session_id, client_id, self.client_limit)
