"""Local administrator identity, independent of versions, seasons and servers.

Names are labels, never ownership keys. No machine identifier is collected and
this file must never be included in distribution or schedule synchronization.
"""
import json
from pathlib import Path
import re
import uuid

from discord_connection_policy import ConnectionPolicy
from runtime_storage import atomic_write

FILENAME = "administrator_identity.json"


def normalize_name(value):
    name = " ".join(str(value or "").split())
    if not name or len(name) > 40 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("담당자 이름은 1~40자로 입력하세요.")
    return name


def _valid_id(value):
    # Preserve exact legacy spelling: ownership records compare strings.
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{32}", value))


def display_identity(identity, *, short=False):
    client = identity["client_id"]
    return f"{identity.get('name') or '담당자 미등록'}_{client[:8] if short else client}"


class AdministratorIdentity:
    def __init__(self, data_root):
        self.path = Path(data_root) / FILENAME
        # Reuse the existing bounded, cross-process file lock only. Connection
        # retry/standby state is still stored separately for each profile.
        self._guard = ConnectionPolicy(self.path)
        self._guard.path = self.path

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise ValueError("담당자 정보를 읽을 수 없습니다. 원본을 보존했으며 새 ID를 발급하지 않았습니다.") from exc
        if (not isinstance(data, dict) or data.get("schema") != 1
                or not _valid_id(data.get("client_id")) or not isinstance(data.get("name"), str)):
            raise ValueError("담당자 정보 형식이 잘못되었습니다. 원본을 확인해 주세요.")
        if data["name"]:
            normalize_name(data["name"])
        return data

    def _save(self, data):
        data["identifier"] = display_identity(data)
        atomic_write(self.path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return dict(data)

    def _legacy_id(self, config_path):
        if config_path is None:
            return ""
        path = Path(str(config_path) + ".connection.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return ""
        except (OSError, ValueError) as exc:
            raise ValueError("기존 담당자 연결 기록을 읽지 못했습니다. 새 ID를 발급하지 않습니다.") from exc
        if not isinstance(data, dict):
            raise ValueError("기존 담당자 연결 기록 형식을 확인하세요.")
        client = data.get("client_id", "")
        if client != "" and not _valid_id(client):
            raise ValueError("기존 담당자 ID 형식을 확인하세요.")
        return client

    def load_or_create(self, legacy_config=None):
        with self._guard._locked():
            data = self._read()
            if data is not None:
                return data
            client = self._legacy_id(legacy_config) or uuid.uuid4().hex
            return self._save(dict(schema=1, client_id=client, name="", guild_name=""))

    def save_name(self, name, guild_name="", legacy_config=None):
        name = normalize_name(name)
        with self._guard._locked():
            data = self._read()
            if data is None:
                client = self._legacy_id(legacy_config) or uuid.uuid4().hex
                data = dict(schema=1, client_id=client)
            data.update(name=name, guild_name=str(guild_name or "").strip())
            return self._save(data)
