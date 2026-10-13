"""perception.clues：家族 → 结构线索表（SPEC-7 §1.2；纯数据零 IO 零逻辑）。

通道 A 的唯一词表源：taxonomy 家族 → sink/source/dangerous/serialization 四档
线索名（小写匹配，调用点与标识符两档消费）。**v1 词表，允许扩充不收缩**——
收缩即漏报面收窄，属规格级变更须走 DECISIONS；扩充（加名）无需拍板。

匹配语义（indexer 消费，本模块只存数据）：
- ``sinks``：命中 → Candidate(kind="sink")；
- ``sources``：命中 → Candidate(kind="source")（标识符/参数名匹配）；
- ``dangerous``：命中 → Candidate(kind="dangerous_call")（弱算法/危险内存函数/
  权限函数等非注入族危险调用）；
- ``serialization``：命中 → Candidate(kind="serialization")（反序列化入口）。

deser 家族不在 v0 题库（SPEC-6 §1.2 v0.1 退出）但仍属 §7.2 家族树——线索表
保留其 serialization 档（通道 A 不缺席该家族），与其格子冷启动走生产存款的
口径一致（DECISIONS 2026-10-11 配额修订行）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["FamilyClues", "CHANNEL_A_CLUES", "clue_families", "match_name"]


@dataclass(frozen=True)
class FamilyClues:
    """单家族四档线索名集合（全部小写；匹配时对被查名做 casefold）。"""

    sinks: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    dangerous: tuple[str, ...] = ()
    serialization: tuple[str, ...] = ()


#: 家族 → 结构线索（SPEC-7 §1.2 初版清单逐字展开；v1 词表允许扩充不收缩）。
CHANNEL_A_CLUES: dict[str, FamilyClues] = {
    "injection": FamilyClues(
        sinks=(
            "system", "exec", "execv", "execve", "popen", "popen2",
            "eval", "execute", "executemany", "rawquery", "query",
            "spawn", "spawnv", "spawnl", "createprocess", "runtime_exec",
            "queryselector", "innerhtml", "document_write",
            "subprocess_call", "subprocess_run", "subprocess_popen",
            "check_output", "start_process",
        ),
        sources=(
            "argv", "args", "request", "get", "post", "input", "stdin",
            "query_string", "querystring", "params", "form", "cookies",
            "readline", "recv", "environ_get",
        ),
    ),
    "deser": FamilyClues(
        serialization=(
            "pickle_load", "pickle_loads", "unpickle", "yaml_load",
            "objectinputstream", "readobject", "unmarshal", "deserialize",
            "jsonparse_unsafe", "marshal_load", "unserialize",
        ),
    ),
    "crypto": FamilyClues(
        dangerous=(
            "md5", "sha1", "des", "rc4", "ecb", "ecbcipher", "descipherspec",
            "new_md5", "new_sha1", "new_des", "md5init", "sha1init",
            "setkey", "hardcoded_key", "hardcoded_secret", "static_iv",
        ),
        sources=(
            "password", "passwd", "secret", "api_key", "apikey", "private_key",
            "hardcoded", "embed_key", "api_secret", "secret_key", "client_secret",
        ),
    ),
    "authz": FamilyClues(
        dangerous=(
            "is_admin", "check_permission", "checkauth", "authorize",
            "has_role", "hasauthority", "validate_token", "verify_token",
            "session_check", "getsession", "isauthenticated",
        ),
        sources=(
            "session", "sessionid", "cookie", "token", "csrf", "role",
            "user_id", "userid", "current_user", "principal",
        ),
    ),
    "config_dep": FamilyClues(
        sources=(
            "password", "passwd", "secret", "api_key", "apikey", "access_key",
            "aws_secret", "private_key", "credential", "token", "auth_token",
            "api_secret", "secret_key", "client_secret",
        ),
        dangerous=(
            "hardcoded_password", "hardcoded_secret", "hardcoded_credential",
            "default_password", "debug_true", "allow_origin_any",
        ),
    ),
    "mem_safety": FamilyClues(
        dangerous=(
            "strcpy", "strcat", "sprintf", "vsprintf", "gets", "memcpy",
            "memmove", "strncpy", "strncat", "alloca", "atoi_unsafe",
            "malloc_unchecked", "realloc",
        ),
        sources=("read_buffer", "recv_buffer", "user_buffer"),
    ),
}


def clue_families() -> tuple[str, ...]:
    """线索表覆盖的家族（taxonomy 序无关，字典序；纯函数）。"""
    return tuple(sorted(CHANNEL_A_CLUES))


def match_name(name: str) -> tuple[tuple[str, str], ...]:
    """被查名（调用名/标识符名）→ 命中的 (family, kind) 列表（纯函数）。

    - ``name`` 须已 casefold（本函数不改性——保证同一输入恒同一输出）；
    - 匹配 = 词表名全等（不做子串猜测——子串匹配制造不可控噪声面，v1 收紧）；
    - kind 档位：sinks→"sink"、sources→"source"、dangerous→"dangerous_call"、
      serialization→"serialization"（SPEC-7 §1.1 kind 词表内取值）；
    - 同名跨家族命中（如 secret 同属 crypto/config_dep）→ 全部命中，逐家族
      各出一候选（归并层按 span 合并、channels 语义不受影响）；
    - 返回按 (family, kind) 字典序（确定性）。"""
    hits: list[tuple[str, str]] = []
    for family, clues in CHANNEL_A_CLUES.items():
        if name in clues.sinks:
            hits.append((family, "sink"))
        if name in clues.sources:
            hits.append((family, "source"))
        if name in clues.dangerous:
            hits.append((family, "dangerous_call"))
        if name in clues.serialization:
            hits.append((family, "serialization"))
    return tuple(sorted(hits))
