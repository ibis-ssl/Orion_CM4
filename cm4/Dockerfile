# このファイルは cm4_sim（シミュレータ用の CM4 相当プロセス）をコンテナへ収める
# 責務を持つ。crane 側の docker/scenario/docker-compose.yaml が
# ghcr.io/ibis-ssl/orion-cm4-sim として参照する。
#
# compose 側の契約（crane docker-compose.yaml の cm4-sim サービス）:
#   - network_mode: host
#   - entrypoint: ["tini", "--"]   -> tini が PATH 上にあること
#   - command: cm4_sim ...         -> バイナリが PATH 上に cm4_sim という名前であること
#                                     （リポジトリ内の cm4/bin/cm4_sim.out とは名前が違う）
#
# network_mode: host が前提である。cm4_sim は 127.0.0.1 上で simulator-cli と
# feedback をやり取りし、feedback 再配信も既定でループバックへ固定するので、
# 独立したネットワーク名前空間に置くと crane からも simulator-cli からも見えない。
#
# ビルド定義は cm4/build.sh 唯一である。ここで g++ の行を書き直すと、実機用の
# setup.sh / update.sh と食い違ったバイナリを配ることになる。

# ---- builder ----
FROM debian:bookworm-slim AS builder

# build.sh は cm4_sim.out 以外（boost::asio を使う実機ブリッジ）もビルドするので
# libboost-dev が要る。cm4_sim.out 自体は boost に依存しない。
RUN apt-get update \
  && apt-get install -y --no-install-recommends g++ libboost-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . /src

# --no-tests: テストは CI (.github/workflows/build.yml) が実行する。
# イメージビルドで UDP ポートを取り合うテストを走らせる必要はない。
RUN ./build.sh --no-tests

# ---- runtime ----
FROM debian:bookworm-slim

# tini は compose 側が entrypoint として指定する。シグナルを cm4_sim へ届け、
# ゾンビを刈る。cm4_sim 自身は SIGINT/SIGTERM で停止する。
RUN apt-get update \
  && apt-get install -y --no-install-recommends tini \
  && rm -rf /var/lib/apt/lists/*

# compose の command が `cm4_sim` と書くので、その名前で PATH に置く。
COPY --from=builder /src/bin/cm4_sim.out /usr/local/bin/cm4_sim

# 特権は要らない。使うポートはすべて 1024 番より上で、multicast 送出にも
# 特権は不要である。
RUN useradd --system --create-home --shell /usr/sbin/nologin cm4sim
USER cm4sim

# 既定の起動。compose は entrypoint と command を上書きするので、これは
# `docker run ghcr.io/ibis-ssl/orion-cm4-sim` 単体で動かすときの挙動である。
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["cm4_sim"]
