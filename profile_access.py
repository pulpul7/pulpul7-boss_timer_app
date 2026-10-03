"""Repair Windows ACL inheritance only inside a validated managed profile."""
import ctypes
import os
from pathlib import Path
import re
import stat
import subprocess
from runtime_storage import atomic_write


def ensure_profile_access(profile, profiles_root, *, legacy=False, force_inheritance=False, repair_denied=True):
    """Keep all file bytes; reset denied items to their parent's ACL.

    No recursive icacls flag, ownership changes, or broad access grants.
    Every item is inspected for reparse points before an ACL can be changed.
    An elevated launch also migrates legacy profile ACLs once, since elevated
    readability does not prove that a later ordinary-user launch can read them.
    """
    if os.name != "nt":
        return False
    root = Path(profiles_root).resolve()
    profile = Path(os.path.abspath(profile))
    try:
        parts = profile.relative_to(root).parts
    except ValueError:
        raise ValueError("서버 프로필 밖의 접근 권한은 변경하지 않습니다.") from None
    valid = (len(parts) == 2 and re.fullmatch(r"season_(?:\d+|unset)", parts[0])
             and re.fullmatch(r"[\w-]+", parts[1], re.ASCII))
    if legacy:
        valid = valid or (len(parts) == 1 and re.fullmatch(r"[\w-]+", parts[0], re.ASCII))
    if not valid:
        raise ValueError("잘못된 서버 프로필 경로입니다.")

    def attributes(path):
        try:
            info = path.lstat()
        except PermissionError:
            # Windows enumeration can inspect a denied child's attributes
            # through its readable parent without opening that child.
            with os.scandir(path.parent) as entries:
                entry = next((item for item in entries if item.name.casefold() == path.name.casefold()), None)
                if entry is None:
                    raise FileNotFoundError(2, "서버 프로필 경로가 없습니다.", str(path))
                info = entry.stat(follow_symlinks=False)
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("링크가 포함된 서버 프로필의 권한은 변경하지 않습니다.")
        if not path.resolve().is_relative_to(root):
            raise ValueError("서버 프로필 밖의 접근 권한은 변경하지 않습니다.")
        return info

    # Inspect every ancestor before following a profile path.
    ancestor = root
    try:
        for part in parts:
            ancestor = ancestor / part
            attributes(ancestor)
    except FileNotFoundError:
        return False

    marker = profile / ".profile_acl_inheritance_v1.json"
    migrate_inheritance = (repair_denied and bool(ctypes.windll.shell32.IsUserAnAdmin())
                           and (force_inheritance or not marker.exists()))

    def reset_inherited(path, error=None):
        if error is not None and getattr(error, "winerror", None) not in (None, 5):
            raise error
        attributes(path)
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/icacls.exe"
        try:
            result = subprocess.run(
                [str(executable), str(path), "/reset", "/Q", "/L"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PermissionError(13, "서버 프로필 접근 권한 복구에 실패했습니다.", str(path)) from exc
        if result.returncode:
            raise PermissionError(13,
                "서버 프로필 접근 권한이 부족합니다. 프로그램을 관리자 권한으로 한 번 실행한 뒤 다시 시도하세요. "
                "기존 설정과 스케줄은 삭제하지 않습니다.", str(path)) from error

    pending = [profile]
    while pending:
        path = pending.pop()
        info = attributes(path)
        if migrate_inheritance:
            reset_inherited(path)
        if stat.S_ISDIR(info.st_mode):
            try:
                with os.scandir(path) as entries:
                    children = [Path(entry.path) for entry in entries]
            except PermissionError as exc:
                if not repair_denied:
                    raise
                reset_inherited(path, exc)
                with os.scandir(path) as entries:
                    children = [Path(entry.path) for entry in entries]
            pending.extend(children)
        elif stat.S_ISREG(info.st_mode):
            try:
                with path.open("rb") as stream:
                    stream.read(1)
            except PermissionError as exc:
                if not repair_denied:
                    raise
                reset_inherited(path, exc)
                with path.open("rb") as stream:
                    stream.read(1)
    if migrate_inheritance:
        atomic_write(marker, b'{"schema":1,"inheritance_restored":true}\n')
    return True


def main():
    """Standalone recovery only; never import or start the GUI or Discord bot."""
    import argparse
    parser = argparse.ArgumentParser(description="BossTimer 시즌 프로필 권한 복구")
    parser.add_argument("--profiles-root", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--force-inheritance", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows에서 실행하세요.")
    if args.force_inheritance and args.check_only:
        parser.error("복구와 읽기 전용 확인을 동시에 지정할 수 없습니다.")
    if args.force_inheritance and not ctypes.windll.shell32.IsUserAnAdmin():
        print("권한 복구 도구에 관리자 승인이 필요합니다.")
        return 3
    try:
        found = ensure_profile_access(args.profile, args.profiles_root,
            force_inheritance=args.force_inheritance, repair_denied=not args.check_only)
        if not found:
            raise FileNotFoundError(2, "복구할 시즌 프로필이 없습니다.", args.profile)
    except (OSError, ValueError) as exc:
        print(str(exc))
        return 2
    print("시즌 프로필 접근 확인 완료" if args.check_only else "시즌 프로필 권한 복구 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
