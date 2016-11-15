#!/usr/bin/env python
"""Invoke the fingerprint client action on a file."""


from grr.client.client_actions import file_fingerprint
from grr.lib import aff4
from grr.lib import flow
from grr.lib.aff4_objects import aff4_grr
from grr.lib.rdfvalues import client as rdf_client
from grr.lib.rdfvalues import structs as rdf_structs
from grr.proto import flows_pb2


class FingerprintFileArgs(rdf_structs.RDFProtoStruct):
  protobuf = flows_pb2.FingerprintFileArgs


class FingerprintFileResult(rdf_structs.RDFProtoStruct):
  protobuf = flows_pb2.FingerprintFileResult


class FingerprintFileMixin(object):
  """Retrieve all fingerprints of a file."""

  fingerprint_file_mixin_client_action = file_fingerprint.FingerprintFile

  def FingerprintFile(self, pathspec, max_filesize=None, request_data=None):
    """Launch a fingerprint client action."""
    request = rdf_client.FingerprintRequest(pathspec=pathspec)
    if max_filesize is not None:
      request.max_filesize = max_filesize

    # Generic hash.
    request.AddRequest(
        fp_type=rdf_client.FingerprintTuple.Type.FPT_GENERIC,
        hashers=[
            rdf_client.FingerprintTuple.HashType.MD5,
            rdf_client.FingerprintTuple.HashType.SHA1,
            rdf_client.FingerprintTuple.HashType.SHA256
        ])

    # Authenticode hash.
    request.AddRequest(
        fp_type=rdf_client.FingerprintTuple.Type.FPT_PE_COFF,
        hashers=[
            rdf_client.FingerprintTuple.HashType.MD5,
            rdf_client.FingerprintTuple.HashType.SHA1,
            rdf_client.FingerprintTuple.HashType.SHA256
        ])

    self.CallClient(
        self.fingerprint_file_mixin_client_action,
        request,
        next_state="ProcessFingerprint",
        request_data=request_data)

  @flow.StateHandler()
  def ProcessFingerprint(self, responses):
    """Store the fingerprint response."""
    if not responses.success:
      # Its better to raise rather than merely logging since it will make it to
      # the flow's protobuf and users can inspect the reason this flow failed.
      raise flow.FlowError("Could not fingerprint file: %s" % responses.status)

    response = responses.First()
    if response.pathspec.path:
      urn = aff4_grr.VFSGRRClient.PathspecToURN(response.pathspec,
                                                self.client_id)
    else:
      urn = aff4_grr.VFSGRRClient.PathspecToURN(self.args.pathspec,
                                                self.client_id)
    self.state.urn = urn

    fd = aff4.FACTORY.Create(urn, aff4_grr.VFSFile, mode="w", token=self.token)

    if response.HasField("hash"):
      hash_obj = response.hash

    else:
      # TODO(user): Deprecate when all clients can send new format
      # responses.
      hash_obj = fd.Schema.HASH()

      for result in response.results:
        if result["name"] == "generic":
          for hash_type in ["md5", "sha1", "sha256"]:
            value = result.GetItem(hash_type)
            if value:
              setattr(hash_obj, hash_type, value)

        if result["name"] == "pecoff":
          for hash_type in ["md5", "sha1", "sha256"]:
            value = result.GetItem(hash_type)
            if value:
              setattr(hash_obj, "pecoff_" + hash_type, value)

          signed_data = result.GetItem("SignedData", [])
          for data in signed_data:
            hash_obj.signed_data.Append(
                revision=data[0], cert_type=data[1], certificate=data[2])

    fd.Set(fd.Schema.HASH, hash_obj)
    fd.Close(sync=True)

    self.ReceiveFileFingerprint(
        urn, hash_obj, request_data=responses.request_data)

  def ReceiveFileFingerprint(self, urn, hash_obj, request_data=None):
    """This method will be called with the new urn and the received hash."""


class FingerprintFile(FingerprintFileMixin, flow.GRRFlow):
  """Retrieve all fingerprints of a file."""

  category = "/Filesystem/"
  args_type = FingerprintFileArgs
  behaviours = flow.GRRFlow.behaviours + "ADVANCED"

  @flow.StateHandler()
  def Start(self):
    """Issue the fingerprinting request."""
    self.FingerprintFile(self.args.pathspec)

  def ReceiveFileFingerprint(self, urn, hash_obj, request_data=None):
    # Notify any parent flows.
    self.SendReply(FingerprintFileResult(file_urn=urn, hash_entry=hash_obj))

  def NotifyAboutEnd(self):
    self.Notify("ViewObject", self.state.urn, "Fingerprint retrieved.")

  @flow.StateHandler()
  def End(self):
    """Finalize the flow."""
    super(FingerprintFile, self).End()

    self.Status("Finished fingerprinting %s", self.args.pathspec.path)
