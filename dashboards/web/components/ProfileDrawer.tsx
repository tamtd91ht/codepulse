"use client";

import Link from "next/link";
import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";

import { Modal } from "@/components/CriteriaParts";
import { Avatar, Loading, Message, Pill } from "@/components/ui";
import { ProfilePermissions } from "@/components/ProfilePermissions";
import { ApiError, api, can, useApi } from "@/lib/api";
import type { CloseQuota, Me, Person } from "@/lib/types";

/** The formats and size the API keeps (app/avatars.py, AVATAR_MAX_KB default 1024). */
const AVATAR_TYPES = ["image/png", "image/jpeg", "image/webp"];
const AVATAR_MAX_KB = 1024;
/** A label above its input, full width (labels are inline rows by default). */
const FIELD = { display: "flex", flexDirection: "column", alignItems: "stretch", gap: 4 } as const;

/** What the API accepts as an email (app/api/schemas.py UserPatch). */
const EMAIL_PATTERN = "[^@\\s]+@[^@\\s]+\\.[^@\\s]+";

/** Admin › Người & Team: one member's profile — name, email and their one avatar (2026-11-03). */
export function ProfileDrawer({ userId, onClose, onChanged }: { userId: number; onClose: () => void; onChanged: () => void }) {
  const { data: person, error, mutate } = useApi<Person>(`/users/${userId}`);
  const { data: me } = useApi<Me>("/auth/me");
  const { mutate: mutateKey } = useSWRConfig();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  // Fill the form once per person: a later refresh (an avatar change, a revalidation on focus)
  // must not overwrite what the admin is typing.
  const seededFor = useRef<number | null>(null);
  useEffect(() => {
    if (person && seededFor.current !== person.id) {
      seededFor.current = person.id;
      setName(person.full_name ?? "");
      setEmail(person.primary_email ?? "");
    }
  }, [person]);

  const fail = (e: unknown) => setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
  const done = async (text: string, fresh: Person) => {
    await mutate(fresh, { revalidate: false });
    // The member page reads the same person from /people/{id}.
    void mutateKey(`/people/${fresh.id}`);
    onChanged();
    setMessage(text);
  };

  const emailChanged = !!person && email.trim().toLowerCase() !== (person.primary_email ?? "").toLowerCase();
  const dirty = !!person && (name.trim() !== (person.full_name ?? "") || emailChanged);

  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!person || !dirty) return;
    if (
      emailChanged &&
      !window.confirm(
        `Đổi email của "${person.full_name ?? person.primary_email}" thành ${email.trim()}? Email được dùng để đăng nhập (khi chưa gắn tài khoản GitLab) và để gắn commit theo email.`,
      )
    )
      return;
    setBusy(true);
    try {
      const body: Record<string, string> = {};
      if (name.trim() !== (person.full_name ?? "")) body.full_name = name.trim();
      if (emailChanged) body.primary_email = email.trim();
      const saved = await api.patch<Person>(`/users/${person.id}`, body);
      setName(saved.full_name ?? "");
      setEmail(saved.primary_email ?? "");
      await done("Đã lưu hồ sơ.", saved);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function pick(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // the same file can be picked again after an error
    if (!file || !person) return;
    if (!AVATAR_TYPES.includes(file.type)) {
      setMessage("Lỗi: chỉ nhận ảnh PNG, JPEG hoặc WebP.");
      return;
    }
    if (file.size > AVATAR_MAX_KB * 1024) {
      setMessage(`Lỗi: ảnh lớn hơn ${AVATAR_MAX_KB} KB.`);
      return;
    }
    setBusy(true);
    try {
      // The raw bytes as the body: the API reads the type from the bytes, not from the header.
      await done("Đã cập nhật ảnh đại diện.", await api.putRaw<Person>(`/users/${person.id}/avatar`, file));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function removeAvatar() {
    if (!person || !window.confirm("Xoá ảnh đại diện? Chỗ ảnh sẽ hiện chữ viết tắt của tên.")) return;
    setBusy(true);
    try {
      await done("Đã xoá ảnh đại diện.", await api.delete<Person>(`/users/${person.id}/avatar`));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    // A dialog in the middle of the screen, wide enough for the permissions' menu and panel side by
    // side (user 2026-11-12: the side drawer pushed them off the right edge).
    <Modal title="Hồ sơ thành viên" onClose={onClose} width={1000}>
      {!person ? (
        <Loading error={error} rows={4} />
      ) : (
        <>
          <Message text={message} onClose={() => setMessage(null)} />
          <div className="toolbar" style={{ alignItems: "center", gap: 16, marginBottom: 16 }}>
            <Avatar name={person.full_name} email={person.primary_email} size="xl" src={person.avatar_url} />
            <div>
              <div>
                <strong>{person.full_name ?? person.primary_email ?? `#${person.id}`}</strong>
              </div>
              <div className="small muted">
                ID {person.id} · {person.active === false ? "đã tắt" : "đang hoạt động"}
                {person.is_admin && <> · Admin</>}
                {person.manage_all && <> · Quản lý tổng</>}
              </div>
              <div className="toolbar" style={{ marginTop: 8 }}>
                <input
                  ref={fileInput}
                  type="file"
                  accept={AVATAR_TYPES.join(",")}
                  onChange={(e) => void pick(e)}
                  hidden
                  aria-label="Chọn ảnh đại diện"
                />
                <button type="button" className="sm" disabled={busy} onClick={() => fileInput.current?.click()}>
                  {person.avatar_url ? "Đổi ảnh" : "Tải ảnh lên"}
                </button>
                {person.avatar_url && (
                  <button type="button" className="sm ghost danger" disabled={busy} onClick={() => void removeAvatar()}>
                    Xoá ảnh
                  </button>
                )}
              </div>
              <div className="small muted">PNG, JPEG hoặc WebP, tối đa {AVATAR_MAX_KB} KB. Mỗi người một ảnh; ảnh mới thay ảnh cũ.</div>
            </div>
          </div>

          <form onSubmit={(e) => void save(e)} style={{ display: "grid", gap: 12 }}>
            <label style={FIELD}>
              <span className="small muted">Họ tên</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required={!!person.full_name || name !== ""}
                maxLength={200}
                autoComplete="off"
              />
            </label>
            <label style={FIELD}>
              <span className="small muted">Email</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required={!!person.primary_email || email !== ""}
                pattern={EMAIL_PATTERN}
                title="Email dạng ten@congty.vn"
                autoComplete="off"
              />
            </label>
            {emailChanged && (
              <p className="small muted" style={{ margin: 0 }}>
                <Pill tone="warn">Lưu ý</Pill> Email dùng để đăng nhập khi chưa gắn tài khoản GitLab và để gắn commit theo email. Email đã có
                người dùng sẽ bị từ chối.
              </p>
            )}
            <div className="toolbar">
              <button type="submit" className="primary" disabled={busy || !dirty}>
                Lưu hồ sơ
              </button>
              <Link href={`/members/${person.id}`} className="small">
                Mở trang thành viên →
              </Link>
            </div>
          </form>
          {can(me, "admin.review_settings") && (
            <section aria-labelledby={`quota-${person.id}`} style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
              <h3 id={`quota-${person.id}`} style={{ margin: "0 0 10px", fontSize: "var(--text-base, 15px)" }}>
                Yêu cầu đóng finding
              </h3>
              <CloseQuotaForm userId={person.id} />
            </section>
          )}
          {can(me, "admin.permissions") && (
            <section aria-labelledby={`perm-${person.id}`} style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
              <h3 id={`perm-${person.id}`} style={{ margin: "0 0 10px", fontSize: "var(--text-base, 15px)" }}>
                Phân quyền
              </h3>
              <ProfilePermissions userId={person.id} onChanged={onChanged} />
            </section>
          )}
        </>
      )}
    </Modal>
  );
}

/**
 * A person's limit of close requests over 24 hours (web and MCP, user 2026-11-06): the default
 * of Admin › Cấu hình, or their own number (0: may send none).
 */
function CloseQuotaForm({ userId }: { userId: number }) {
  const { data, error, mutate } = useApi<CloseQuota>(`/admin/users/${userId}/close-quota`);
  const [mode, setMode] = useState<"default" | "own">("default");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const seeded = useRef(false);
  useEffect(() => {
    if (data && !seeded.current) {
      seeded.current = true;
      setMode(data.own === null ? "default" : "own");
      setValue(String(data.own ?? data.default));
    }
  }, [data]);
  if (!data) return <Loading error={error} rows={2} />;

  const wanted = mode === "default" ? null : Number(value);
  const valid = mode === "default" || (value !== "" && Number.isInteger(wanted) && wanted! >= 0 && wanted! <= 1000);
  const dirty = wanted !== data.own;

  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!valid || !dirty) return;
    setBusy(true);
    setMessage(null);
    try {
      const saved = await api.put<CloseQuota>(`/admin/users/${userId}/close-quota`, { own: wanted });
      await mutate(saved, { revalidate: false });
      setMessage(saved.own === null ? `Đã về mặc định (${saved.default}).` : `Đã đặt riêng ${saved.own} yêu cầu / 24 giờ.`);
    } catch (err) {
      setMessage(`Lỗi: ${err instanceof ApiError ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  const full = data.limit > 0 && data.used_24h >= data.limit;
  return (
    <form onSubmit={(e) => void save(e)} style={{ display: "grid", gap: 10 }}>
      <div className="small">
        Đã gửi <strong>{data.used_24h}</strong> / {data.limit === 0 ? "0 (không được gửi)" : data.limit} trong 24 giờ qua{" "}
        {full && <Pill tone="warn">Đã hết lượt</Pill>}
      </div>
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input type="radio" name={`quota-mode-${userId}`} checked={mode === "default"} onChange={() => setMode("default")} />
        <span>Theo mặc định ({data.default} / 24 giờ, Admin › Cấu hình)</span>
      </label>
      <label style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input type="radio" name={`quota-mode-${userId}`} checked={mode === "own"} onChange={() => setMode("own")} />
        <span>Riêng cho người này:</span>
        <input
          type="number"
          min={0}
          max={1000}
          step={1}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setMode("own");
          }}
          style={{ width: 96 }}
          aria-label="Số yêu cầu đóng tối đa trong 24 giờ"
        />
        <span className="small muted">/ 24 giờ</span>
      </label>
      <p className="small muted" style={{ margin: 0 }}>
        Tính chung web và MCP, cửa sổ 24 giờ trượt. 0 = không được gửi yêu cầu đóng.
      </p>
      <div className="toolbar">
        <button type="submit" className="sm" disabled={busy || !valid || !dirty}>
          Lưu giới hạn
        </button>
        {message && (
          <span className={message.startsWith("Lỗi") ? "small" : "small muted"} style={message.startsWith("Lỗi") ? { color: "var(--bad-text)" } : undefined}>
            {message}
          </span>
        )}
      </div>
    </form>
  );
}

