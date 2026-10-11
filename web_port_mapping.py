"""Owned IPv4 UPnP mappings. Never replace another application's mapping."""
from __future__ import annotations

import ipaddress
import re
import urllib.error
from xml.etree import ElementTree as ET

from web_network_diagnostics import _fetch, _router_url, _text, _xml

LEASE_SECONDS = 3600


class MappingError(Exception):
    def __init__(self, code):
        self.code = int(code)
        super().__init__("UPnP action failed")


def validate_mapping(data):
    if not isinstance(data, dict):
        raise ValueError("invalid mapping")
    for name in ("gateway", "client"):
        address = ipaddress.ip_address(data[name])
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified:
            raise ValueError("invalid local route")
    _router_url(data["control"], data["gateway"])
    if not re.fullmatch(r"urn:schemas-upnp-org:service:WAN(?:IP|PPP)Connection:[12]", data["service"]):
        raise ValueError("invalid service")
    if not re.fullmatch(r"BossTimerWeb-[0-9a-f]{32}", data["description"]):
        raise ValueError("invalid ownership marker")
    for name in ("external_port", "internal_port"):
        if type(data[name]) is not int or not 1 <= data[name] <= 65535:
            raise ValueError("invalid port")
    return data


def soap(mapping, action, arguments):
    validate_mapping(mapping)
    envelope = ET.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope",
                          {"s:encodingStyle": "http://schemas.xmlsoap.org/soap/encoding/", "xmlns:s": "http://schemas.xmlsoap.org/soap/envelope/"})
    body = ET.SubElement(envelope, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
    command = ET.SubElement(body, "{" + mapping["service"] + "}" + action)
    for name, value in arguments.items():
        ET.SubElement(command, name).text = str(value)
    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{mapping["service"]}#{action}"'}
    try:
        response = _fetch(mapping["control"], body=ET.tostring(envelope, encoding="utf-8", xml_declaration=True), headers=headers)
    except urllib.error.HTTPError as exc:
        try:
            response = exc.read(16385)
        finally:
            exc.close()
        if len(response) > 16384:
            raise ValueError("oversized router fault")
    root = _xml(response)
    error = _text(root, "errorCode")
    if error:
        if not error.isdigit():
            raise ValueError("invalid router fault")
        raise MappingError(int(error))
    if not any(node.tag == "{" + mapping["service"] + "}" + action + "Response" for node in root.iter()):
        raise ValueError("unexpected router response")
    return root


def specific(mapping):
    try:
        root = soap(mapping, "GetSpecificPortMappingEntry", dict(NewRemoteHost="",
                    NewExternalPort=mapping["external_port"], NewProtocol="TCP"))
    except MappingError as exc:
        if exc.code == 714:
            return None
        raise
    return dict(client=_text(root, "NewInternalClient"), port=int(_text(root, "NewInternalPort")),
                description=_text(root, "NewPortMappingDescription"), enabled=_text(root, "NewEnabled").lower() in {"1", "true"})


def owned(mapping, entry):
    return bool(entry and entry["client"] == mapping["client"] and entry["port"] == mapping["internal_port"]
                and entry["description"] == mapping["description"])


def add(mapping):
    existing = specific(mapping)
    if existing and not owned(mapping, existing):
        raise MappingError(718)
    arguments = dict(NewRemoteHost="", NewExternalPort=mapping["external_port"], NewProtocol="TCP",
                     NewInternalPort=mapping["internal_port"], NewInternalClient=mapping["client"],
                     NewEnabled=1, NewPortMappingDescription=mapping["description"], NewLeaseDuration=LEASE_SECONDS)
    try:
        soap(mapping, "AddPortMapping", arguments)
        permanent = False
    except MappingError as exc:
        if exc.code != 725:
            raise
        arguments["NewLeaseDuration"] = 0
        soap(mapping, "AddPortMapping", arguments)
        permanent = True
    entry = specific(mapping)
    if not owned(mapping, entry) or not entry["enabled"]:
        raise ValueError("mapping verification failed")
    return permanent


def remove(mapping):
    entry = specific(mapping)
    if not owned(mapping, entry):
        return  # Missing or now owned by someone else: never delete it.
    try:
        soap(mapping, "DeletePortMapping", dict(NewRemoteHost="", NewExternalPort=mapping["external_port"], NewProtocol="TCP"))
    except MappingError as exc:
        if exc.code != 714:
            raise
    if owned(mapping, specific(mapping)):
        raise ValueError("mapping removal not confirmed")
