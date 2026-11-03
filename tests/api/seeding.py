"""Seeding MRs, notes and accounts for the insights / evidence API tests (a helper module, not
a test module: importing a test module drags its fixtures out of scope)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Account, Commit, MergeRequest, MrCommit, MrNote, Project, User

MON = datetime(2026, 10, 5, 2, tzinfo=UTC)  # 09:00 in Asia/Ho_Chi_Minh


def at(hours: float) -> datetime:
    return MON + timedelta(hours=hours)


class Seeder:
    def __init__(self, session: Session, project: Project) -> None:
        self.s = session
        self.project = project
        self.accounts: dict[int, Account] = {}
        self.next_iid = 100
        self.next_note = 1

    def account(self, user: User | None, key: str) -> Account:
        acc = Account(
            kind="gitlab_user",
            external_key=key,
            display_name=key,
            user_id=user.id if user else None,
        )
        self.s.add(acc)
        self.s.flush()
        return acc

    def mr(
        self,
        author: Account,
        *,
        opened: float,
        merged: float | None,
        first_commit: float | None = None,
        size: int = 100,
        title: str = "Change",
    ) -> MergeRequest:
        self.next_iid += 1
        mr = MergeRequest(
            project_id=self.project.id,
            iid=self.next_iid,
            title=title,
            author_account_id=author.id,
            state="merged" if merged is not None else "opened",
            target_branch="main",
            created_at_src=at(opened),
            merged_at=at(merged) if merged is not None else None,
            additions=size,
            deletions=0,
            review_rounds=1,
        )
        self.s.add(mr)
        self.s.flush()
        if first_commit is not None:
            sha = f"{self.next_iid:040d}"
            self.s.add(Commit(sha=sha, project_id=self.project.id, authored_at=at(first_commit)))
            self.s.flush()
            self.s.add(MrCommit(mr_id=mr.id, sha=sha))
        return mr

    def note(
        self,
        mr: MergeRequest,
        who: Account,
        hours: float,
        body: str = "LGTM",
        *,
        system: bool = False,
    ) -> None:
        self.next_note += 1
        self.s.add(
            MrNote(
                id=900_000 + self.next_note,
                mr_id=mr.id,
                author_account_id=who.id,
                body=body,
                is_system=system,
                created_at_src=at(hours),
            )
        )
