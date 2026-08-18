# このファイルは machine_no <-> IP の変換規則を一元管理する。
# 既存の host/lib/cm4_control_client.py, host/lib/cm4_camera_client.py,
# host/robot-manager/server.py に散在していた "192.168.20.100+N" 規則の正本。
import json
import os
from dataclasses import dataclass

IP_BASE = "192.168.20."
IP_OFFSET = 100
DEFAULT_MACHINE_COUNT = 13
SSH_PORT = 22
SSH_USER = "ibis"
CONTROL_PORT = 8000

INVENTORY_ENV_VAR = "ORION_FLEET_INVENTORY"


@dataclass
class Host:
    machine_no: int
    ip: str
    ssh_user: str = SSH_USER
    ssh_port: int = SSH_PORT
    control_port: int = CONTROL_PORT


def ip_for_machine(machine_no, ip_base=IP_BASE, ip_offset=IP_OFFSET):
    return f"{ip_base}{ip_offset + machine_no}"


def default_inventory(count=DEFAULT_MACHINE_COUNT):
    return [Host(machine_no=n, ip=ip_for_machine(n)) for n in range(count)]


def default_ip_list(count=DEFAULT_MACHINE_COUNT):
    return [host.ip for host in default_inventory(count)]


def load_inventory(path=None):
    path = path or os.environ.get(INVENTORY_ENV_VAR)
    if not path:
        return default_inventory()

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    ip_base = data.get("ip_base", IP_BASE)
    ip_offset = data.get("ip_offset", IP_OFFSET)

    hosts = []
    for entry in data.get("hosts", []):
        machine_no = entry["machine_no"]
        ip = entry.get("ip") or ip_for_machine(machine_no, ip_base, ip_offset)
        hosts.append(
            Host(
                machine_no=machine_no,
                ip=ip,
                ssh_user=entry.get("ssh_user", SSH_USER),
                ssh_port=entry.get("ssh_port", SSH_PORT),
                control_port=entry.get("control_port", CONTROL_PORT),
            )
        )
    return hosts


def _parse_number_ranges(spec):
    """'0,1,5-8' のような指定を {0,1,5,6,7,8} に変換する。"""
    result = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            result.update(range(int(start), int(end) + 1))
        else:
            result.add(int(part))
    return result


def resolve_targets(hosts, *, all_=False, machines=None, ips=None):
    """--all / --machines "0,1,5-8" / --ips "a,b" のいずれか一つから対象ホスト一覧を解決する。

    --ips で指定された IP がインベントリに無い場合は、machine_no=-1 のホストとして
    その場で作成する(インベントリ外の一時的な対象を明示指定できるようにするため)。
    """
    selectors = [bool(all_), bool(machines), bool(ips)]
    if sum(selectors) != 1:
        raise ValueError("--all / --machines / --ips のいずれか一つを指定してください")

    if all_:
        return list(hosts)

    if machines:
        wanted = _parse_number_ranges(machines)
        by_no = {host.machine_no: host for host in hosts}
        missing = wanted - by_no.keys()
        if missing:
            raise ValueError(f"インベントリに存在しない machine_no です: {sorted(missing)}")
        return [by_no[no] for no in sorted(wanted)]

    wanted_ips = [ip.strip() for ip in ips.split(",") if ip.strip()]
    by_ip = {host.ip: host for host in hosts}
    return [by_ip.get(ip, Host(machine_no=-1, ip=ip)) for ip in wanted_ips]
