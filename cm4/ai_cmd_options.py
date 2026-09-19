# このファイルは lancher.py が ai_cmd_v2.out に渡す任意オプションを、機体ごとの設定ファイルから組み立てる。
# lancher は起動引数を固定で持つため、安全停止のタイムアウトなどを再ビルドなしで機体ごとに調整できるようにする。
# FastAPI に依存しない純粋関数にして、PC 上で単体テストできるようにしている。
import json
import os

# 設定ファイルのキー -> ai_cmd_v2.out のオプション。ここに無いキーは受け付けない
# (typo や、安全に関わる未想定の引数を黙って通さないため)。
# 値はすべて整数 (ms または Hz)。下限は ai_cmd_v2.out 側の検査と揃えている。
ALLOWED_OPTIONS = {
    "command_timeout_ms": ("--command-timeout-ms", 1),
    "feedback_timeout_ms": ("--feedback-timeout-ms", 1),
    "tx_rate_hz": ("--tx-rate-hz", 1),
    "g474_silence_ms": ("--g474-silence-ms", 0),
    "g474_recovery_ms": ("--g474-recovery-ms", 1),
}

CONFIG_FILE_NAME = "ai_cmd_v2_options.json"


def load_ai_cmd_options(runtime_dir, log=print):
    """runtime_dir/ai_cmd_v2_options.json から ai_cmd_v2.out の追加引数を返す。

    ファイルが無ければ [] (= ai_cmd_v2.out の既定値)。
    壊れたファイル・未知のキー・範囲外の値は「全体を無視して既定値」にする。
    一部だけ採用すると、意図しない組み合わせ (例: feedback のみ短い) で起動しうるため。
    無視した理由は log に出す (lancher の標準出力 = journal に残る)。
    """
    path = os.path.join(runtime_dir, CONFIG_FILE_NAME)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        log(f"{path} を読めないため ai_cmd_v2.out の既定値で起動します: {exc}")
        return []
    if not isinstance(data, dict):
        log(f"{path} は JSON オブジェクトではないため無視します")
        return []

    args = []
    for key, value in data.items():
        if key not in ALLOWED_OPTIONS:
            log(f"{path} に未知のキー {key!r} があるため設定全体を無視します (使えるキー: {sorted(ALLOWED_OPTIONS)})")
            return []
        option, minimum = ALLOWED_OPTIONS[key]
        # bool は int のサブクラスなので明示的に除く (true を 1 ms と読まない)。
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            log(f"{path} の {key}={value!r} は {minimum} 以上の整数ではないため設定全体を無視します")
            return []
        args += [option, str(value)]
    if args:
        log(f"ai_cmd_v2.out の追加オプション: {' '.join(args)}")
    return args
