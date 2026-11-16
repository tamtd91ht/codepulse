"""docs/specs/31 §5: task keys in git text."""

from app.tasks import keys as tk


def test_commit_relations() -> None:
    assert tk.commit_refs("feat(contact): đồng bộ danh bạ [CRM-12]") == {"CRM-12": "refs"}
    assert tk.commit_refs("fix(export): căn đúng cột [CRM-15]") == {"CRM-15": "fixes"}
    msg = "chore: dọn dẹp [CRM-1]\n\nRefs: CRM-2\nFixes: CRM-3, CRM-4"
    assert tk.commit_refs(msg) == {
        "CRM-1": "refs",
        "CRM-2": "refs",
        "CRM-3": "fixes",
        "CRM-4": "fixes",
    }
    assert tk.commit_refs("this closes CRM-9 at last") == {"CRM-9": "fixes"}
    assert tk.commit_refs('Revert "fix: x [CRM-5]"\n\nFixes: CRM-5') == {"CRM-5": "refs"}
    # Not keys: lower case in a message, UTF-8, a key glued to a word.
    assert tk.commit_refs("crm-1 utf-8 xCRM-2") == {}
    assert tk.commit_refs("UTF-8 SHA-256") == {"UTF-8": "refs", "SHA-256": "refs"}  # filtered later
    assert tk.commit_refs(None) == {}


def test_mr_and_branch() -> None:
    assert tk.branch_keys("feature/crm-12-login") == ["CRM-12"]
    assert tk.branch_keys("bugfix/CRM-15_export") == ["CRM-15"]
    assert tk.mr_refs("Đăng nhập OTP", "Liên quan CRM-3\n\nCloses CRM-4", "feature/CRM-12-otp") == {
        "CRM-3": "refs",
        "CRM-4": "fixes",
        "CRM-12": "fixes",
    }


def test_smart_commands() -> None:
    cmd = tk.smart_commands("feat: x [CRM-1] #time 1h30m #comment xong phần API #in-review")
    assert cmd.minutes == 90
    assert cmd.comment == "xong phần API"
    assert cmd.statuses == ["in-review"]
    assert tk.smart_commands("#time 2d 1.5h").minutes == 2 * 480 + 90
    assert tk.smart_commands("plain message").statuses == []
    assert tk.slug("Won't Do") == "won-t-do"
    assert tk.slug("In Progress") == "in-progress"


def test_branch_name() -> None:
    assert (
        tk.branch_name("CRM-12", "Đăng nhập bằng OTP!", "story")
        == "feature/CRM-12-dang-nhap-bang-otp"
    )
    assert tk.branch_name("CRM-7", "Lỗi export", "bug") == "bugfix/CRM-7-loi-export"
    assert len(tk.branch_name("CRM-1", "x" * 200, "task")) <= 60
