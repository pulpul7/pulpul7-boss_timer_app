# 시즌 프로필 접근 권한 오류

`server_profiles/season_22/9/init/schedule_boss_definitions.txt` 확인 중
WinError 5가 발생했으며, 읽기 전용 점검에서도 `season_22/9` 자체의 접근이 거부됐다.
이는 인계 기록이나 서버 이름 확인과 별개의 로컬 파일 접근 문제다.

기존 생성 코드는 `TemporaryDirectory` 아래의 프로필·롤백 폴더를 그대로 이름 변경하거나,
그 안의 파일을 하드 링크로 게시했다. 임시 폴더의 제한된 ACL이 게시 후에도 남을 수 있다.
Windows의 `mkdir(mode=0o700)`는 현재 사용자와 관리자만 허용하는 ACL을 적용한다.
이 동작은 Python 3.12.4에도 반영됐다.
근거: [Python 3.12 os.mkdir 문서](https://docs.python.org/3.12/library/os.html#os.mkdir).

수정 사항:

- 영구 데이터용 임시 폴더는 해당 데이터 부모 아래에서 일반 상속 권한으로 생성한다.
  Windows에서 광범위한 사용자 그룹에 별도 권한을 부여하지 않는다.
- 현재 프로필과 이관에 사용되는 프로필의 접근을 확인한다. 거부된 항목은 부모 ACL로
  재설정한다. 관리자 실행 시 아직 복구 기록이 없는 기존 프로필은 상속 권한을 한 번
  정리하고 `.profile_acl_inheritance_v1.json`을 남긴다. 관리자 계정의 읽기 성공만으로
  다음 일반 실행에서도 접근 가능하다고 판단하지 않는다.
  설정·스케줄·롤백 파일 내용은 변경하거나 삭제하지 않는다.
- 재설정은 검증된 서버 프로필 안에서 한 항목씩 수행한다. 링크·정션을 거부하고
  icacls의 재귀 옵션과 소유권 변경을 사용하지 않는다.
- 복구 권한이 부족하면 관리자 권한으로 한 번 실행하도록 안내한다. 시작 단계의 오류도
  자체 중앙 팝업에 표시하고 종료한다. 읽을 수 없는 설정을 기본값으로 대체하지 않는다.

실제 AppData 권한 복구·삭제는 수행하지 않았다. 실행 테스트와 빌드는 사용자가 진행한다.

## 일반 권한의 개발 환경에서 복구하기

VS Code 전체를 관리자 권한으로 실행하지 않아도 된다. 현재 디버깅을 종료하고
프로젝트 터미널에서 다음 명령을 실행한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\repair_profile_access.ps1 -Season 22 -ServerId 9
```

Windows 승인창에서 복구 도구만 관리자 권한으로 실행한다. 지정한 시즌·서버 프로필의
권한만 정리하며 GUI, 봇, Discord 접속은 시작하지 않는다. 완료 후 원래 일반 권한
터미널에서 읽기 전용 확인을 수행한다. 성공하면 같은 개발 환경에서 F5로 다시 실행한다.
Python을 찾지 못하면 `-PythonPath`에 디버깅에 사용하는 Python 실행 파일을 지정한다.
관리자 승인을 거절하면 복구는 진행하지 않는다.
근거: [Microsoft Start-Process 문서](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process?view=powershell-5.1).
