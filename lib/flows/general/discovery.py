#!/usr/bin/env python
# Copyright 2010 Google Inc.
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

"""These are flows designed to discover information about the host."""


import os

from grr.lib import aff4
from grr.lib import constants
from grr.lib import flow
from grr.lib import rdfvalue
from grr.lib import type_info
from grr.lib import utils


class EnrolmentInterrogateEvent(flow.EventListener):
  """An event handler which will schedule interrogation on client enrollment."""
  EVENTS = ["ClientEnrollment"]
  well_known_session_id = "aff4:/flows/W:Interrogate"

  @flow.EventHandler(source_restriction="CA")
  def ProcessMessage(self, message=None, event=None):
    _ = message
    flow.FACTORY.StartFlow(event.common_name, "Interrogate", token=self.token)


class Interrogate(flow.GRRFlow):
  """Interrogate various things about the host."""

  category = "/Administrative/"
  client = None

  flow_typeinfo = type_info.TypeDescriptorSet(
      type_info.Bool(
          description="Perform a light weight version of the interrogate.",
          name="lightweight",
          default=False)
      )

  @flow.StateHandler(next_state=["Hostname", "Platform",
                                 "InstallDate", "EnumerateUsers",
                                 "EnumerateInterfaces", "EnumerateFilesystems",
                                 "ClientInfo", "ClientConfig",
                                 "ClientConfiguration", "VerifyUsers"])
  def Start(self):
    """Start off all the tests."""
    # Create the objects we need to exist.
    self.Load()
    fd = aff4.FACTORY.Create(self.client.urn.Add("network"), "Network",
                             token=self.token)
    fd.Close()

    self.CallClient("GetPlatformInfo", next_state="Platform")
    self.CallClient("GetInstallDate", next_state="InstallDate")
    self.CallClient("GetClientInfo", next_state="ClientInfo")

    # Support both new and old clients.
    self.CallClient("GetConfig", next_state="ClientConfig")
    self.CallClient("GetConfiguration", next_state="ClientConfiguration")

    if not self.lightweight:
      self.sid_data = {}
      profiles_key = (r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft"
                      r"\Windows NT\CurrentVersion\ProfileList")

      request = rdfvalue.RDFFindSpec(path_regex="ProfileImagePath", max_depth=2)

      request.pathspec.path = profiles_key
      request.pathspec.pathtype = request.pathspec.REGISTRY

      # Download all the Registry keys, it is limited by max_depth.
      request.iterator.number = 10000
      self.CallClient("Find", request, next_state="VerifyUsers")
      self.CallClient("EnumerateInterfaces", next_state="EnumerateInterfaces")
      self.CallClient("EnumerateFilesystems", next_state="EnumerateFilesystems")

  def Load(self):
    # Ensure there is a client object
    self.client = aff4.FACTORY.Open(self.client_id, mode="rw", token=self.token)

  def Save(self):
    # Make sure the client object is removed and closed
    if self.client:
      self.client.Close()
      self.client = None

  @flow.StateHandler(next_state=["GetFolders", "EnumerateUsers"])
  def VerifyUsers(self, responses):
    """Issues WMI queries that verify that the SIDs actually belong to users."""
    if not responses.success:
      self.Log("Cannot query registry for user information, "
               " using EnumerateUsers.")
      self.CallClient("EnumerateUsers", next_state="EnumerateUsers")
      return

    for response in responses:
      if response.hit.resident:
        # Support old clients.
        homedir = utils.SmartUnicode(response.hit.resident)
      else:
        homedir = response.hit.registry_data.GetValue()
      # Cut away ProfilePath
      path = os.path.dirname(response.hit.pathspec.path)
      sid = os.path.basename(path)
      # This is the best way we have of deriving the username from a SID.
      user = homedir[homedir.rfind("\\") + 1:]
      self.sid_data[sid] = {"homedir": homedir,
                            "username": user,
                            "sid": sid}
      query = "SELECT * FROM Win32_UserAccount WHERE name=\"%s\"" % user
      self.CallClient("WmiQuery", query=query, next_state="GetFolders",
                      request_data={"SID": sid})

  @flow.StateHandler(next_state=["VerifyFolders"])
  def GetFolders(self, responses):
    """If the SID belongs to a user, this tries to get the special folders."""
    if not responses.success:
      return

    for acc in responses:
      # It could happen that wmi returns an AD user with the same name as the
      # local one. In this case, we just ignore the unknown SID.
      if acc["SID"] not in self.sid_data.iterkeys():
        continue

      self.sid_data[acc["SID"]]["domain"] = acc["Domain"]
      folder_path = (r"HKEY_USERS\%s\Software\Microsoft\Windows"
                     r"\CurrentVersion\Explorer\Shell Folders") % acc["SID"]
      self.CallClient("ListDirectory",
                      pathspec=rdfvalue.RDFPathSpec(
                          path=folder_path,
                          pathtype=rdfvalue.RDFPathSpec.Enum("REGISTRY")),
                      request_data=responses.request_data,
                      next_state="VerifyFolders")

  @flow.StateHandler(next_state=["SaveFolders"])
  def VerifyFolders(self, responses):
    """This saves the returned folders."""
    if responses.success:
      profile_folders = {}
      for response in responses:
        returned_folder = os.path.basename(response.pathspec.path)
        for (folder, _, pb_field) in constants.profile_folders:
          if folder == returned_folder:
            profile_folders[pb_field] = (
                response.resident or
                response.registry_data.GetValue())
            break
      # Save the user pb.
      data = self.sid_data[responses.request_data["SID"]]
      data["special_folders"] = rdfvalue.FolderInformation(**profile_folders)

      if self.OutstandingRequests() == 1:
        # This is the last response -> save all data.
        self.SaveUsers()

    else:
      # Reading from registry failed, we have to guess.
      homedir = self.sid_data[responses.request_data["SID"]]["homedir"]
      for (folder, subdirectory, pb_field) in constants.profile_folders:
        data = responses.request_data
        data["pb_field"] = pb_field
        self.CallClient("StatFile",
                        pathspec=rdfvalue.RDFPathSpec(
                            path=utils.JoinPath(homedir,
                                                subdirectory),
                            pathtype=rdfvalue.RDFPathSpec.Enum("OS")),
                        request_data=data,
                        next_state="SaveFolders")

  @flow.StateHandler()
  def SaveFolders(self, responses):
    """Saves all the folders found to the user pb."""
    if responses.success:
      profile_folders = {}
      for response in responses:
        path = response.pathspec.path
        # We want to store human readable Windows paths here.
        path = path.lstrip("/").replace("/", "\\")
        profile_folders[responses.request_data["pb_field"]] = path
      data = self.sid_data[responses.request_data["SID"]]
      folder = rdfvalue.FolderInformation(**profile_folders)
      try:
        data["special_folders"].MergeFrom(folder.ToProto())
      except KeyError:
        data["special_folders"] = folder

    if self.OutstandingRequests() == 1:
      # This is the last response -> save all the data.
      self.SaveUsers()

  def SaveUsers(self):
    """This saves the collected data to the data store."""
    user_list = self.client.Schema.USER()
    usernames = []
    for sid in self.sid_data.iterkeys():
      data = self.sid_data[sid]
      usernames.append(data["username"])
      user_list.Append(rdfvalue.User(**data))
      self.client.AddAttribute(self.client.Schema.USER, user_list)
    self.client.AddAttribute(self.client.Schema.USERNAMES(
        " ".join(sorted(usernames))))

  @flow.StateHandler()
  def Platform(self, responses):
    """Stores information about the platform."""
    if responses.success:
      response = responses.First()

      # These need to be in separate attributes because they get searched on in
      # the GUI
      self.client.Set(self.client.Schema.HOSTNAME(response.node))
      self.client.Set(self.client.Schema.SYSTEM(response.system))
      self.client.Set(self.client.Schema.OS_RELEASE(response.release))
      self.client.Set(self.client.Schema.OS_VERSION(response.version))

      # response.machine is the machine value of platform.uname()
      # On Windows this is the value of:
      # HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Session
      # Manager\Environment\PROCESSOR_ARCHITECTURE
      # "AMD64", "IA64" or "x86"
      self.client.Set(self.client.Schema.ARCH(response.machine))
      self.client.Set(self.client.Schema.UNAME("%s-%s-%s" % (
          response.system, response.release, response.version)))

      # Windows systems get registry hives
      if response.system == "Windows":
        fd = self.client.CreateMember("registry", "VFSDirectory")
        fd.Set(fd.Schema.PATHSPEC, fd.Schema.PATHSPEC(
            path="/", pathtype=rdfvalue.RDFPathSpec.Enum("REGISTRY")))
        fd.Close()
    else:
      self.Log("Could not retrieve Platform info.")

  @flow.StateHandler()
  def InstallDate(self, responses):
    if responses.success:
      response = responses.First()
      self.client.Set(self.client.Schema.INSTALL_DATE(
          response.integer * 1000000))
    else:
      self.Log("Could not get InstallDate")

  @flow.StateHandler()
  def EnumerateUsers(self, responses):
    """Store all users in the data store and maintain indexes."""
    if responses.success and responses:
      usernames = []
      user_list = self.client.Schema.USER()
      # Add all the users to the client object
      for response in responses:
        user_list.Append(response)
        if response.username:
          usernames.append(response.username)

      # Store it now
      self.client.AddAttribute(self.client.Schema.USER, user_list)
      self.client.AddAttribute(self.client.Schema.USERNAMES(
          " ".join(usernames)))
    else:
      self.Log("Could not enumerate users")

  @flow.StateHandler()
  def EnumerateInterfaces(self, responses):
    """Enumerates the interfaces."""
    if responses.success and responses:
      net_fd = self.client.CreateMember("network", "Network")
      interface_list = net_fd.Schema.INTERFACES()
      mac_addresses = []
      for response in responses:
        interface_list.Append(response)

        # Add a hex encoded string for searching
        if response.mac_address != "\x00" * len(response.mac_address):
          mac_addresses.append(response.mac_address.encode("hex"))

      self.client.Set(self.client.Schema.MAC_ADDRESS(
          "\n".join(mac_addresses)))
      net_fd.Set(net_fd.Schema.INTERFACES, interface_list)
      net_fd.Close()
    else:
      self.Log("Could not enumerate interfaces.")

  @flow.StateHandler()
  def EnumerateFilesystems(self, responses):
    """Store all the local filesystems in the client."""
    if responses.success and len(responses):
      filesystems = self.client.Schema.FILESYSTEM()
      for response in responses:
        filesystems.Append(response)

        if response.type == "partition":
          (device, offset) = response.device.rsplit(":", 1)

          offset = int(offset)

          pathspec = rdfvalue.RDFPathSpec(
              path=device, pathtype=rdfvalue.RDFPathSpec.Enum("OS"),
              offset=offset)

          pathspec.Append(path="/",
                          pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))

          urn = self.client.PathspecToURN(pathspec, self.client.urn)
          fd = self.client.CreateMember(urn, "VFSDirectory")
          fd.Set(fd.Schema.PATHSPEC(pathspec))
          fd.Close()
          continue

        if response.device:
          # Create the raw device
          urn = "devices/%s" % response.device

          pathspec = rdfvalue.RDFPathSpec(
              path=response.device, pathtype=rdfvalue.RDFPathSpec.Enum("OS"))

          pathspec.Append(path="/",
                          pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))

          fd = self.client.CreateMember(urn, "VFSDirectory")
          fd.Set(fd.Schema.PATHSPEC(pathspec))
          fd.Close()

          # Create the TSK device
          urn = self.client.PathspecToURN(pathspec, self.client.urn)
          fd = self.client.CreateMember(urn, "VFSDirectory")
          fd.Set(fd.Schema.PATHSPEC(pathspec))
          fd.Close()

        if response.mount_point:
          # Create the OS device
          pathspec = rdfvalue.RDFPathSpec(
              path=response.mount_point,
              pathtype=rdfvalue.RDFPathSpec.Enum("OS"))

          urn = self.client.PathspecToURN(pathspec, self.client.urn)
          fd = self.client.CreateMember(urn, "VFSDirectory")
          fd.Set(fd.Schema.PATHSPEC(pathspec))
          fd.Close()

      self.client.Set(self.client.Schema.FILESYSTEM, filesystems)
    else:
      self.Log("Could not enumerate file systems.")

  @flow.StateHandler()
  def ClientInfo(self, responses):
    """Obtain some information about the GRR client running."""
    if responses.success:
      response = responses.First()
      self.client.Set(self.client.Schema.CLIENT_INFO(response))
    else:
      self.Log("Could not get ClientInfo.")

  @flow.StateHandler()
  def ClientConfig(self, responses):
    """Process client config."""
    if responses.success:
      response = responses.First()
      self.client.Set(self.client.Schema.GRR_CONFIG(response))

  @flow.StateHandler()
  def ClientConfiguration(self, responses):
    """Process client config."""
    if responses.success:
      response = responses.First()
      self.client.Set(self.client.Schema.GRR_CONFIGURATION(response))

  @flow.StateHandler()
  def End(self):
    """Finalize client registration."""
    # Create the bare VFS with empty virtual directories.
    fd = self.client.CreateMember("processes", "ProcessListing")
    fd.Close()

    self.Notify("Discovery", self.client.urn, "Client Discovery Complete")

    # Flush the data to the data store.
    self.client.Close()
