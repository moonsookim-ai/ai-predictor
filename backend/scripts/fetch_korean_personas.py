"""
Nemotron-Personas-Korea 표본 다운로드

전체 데이터셋（100만 행, 약 2GB）을 받지 않고 Hugging Face datasets-server API로
무작위 위치의 행을 표본 추출해 JSONL로 저장한다. 시뮬레이션의
「한국인 가상 소비자 패널」은 이 파일에서 타깃 고객에 맞는 페르소나를 고른다.

데이터 출처: NVIDIA, Nemotron-Personas-Korea (CC BY 4.0)
https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea

사용법:
    python scripts/fetch_korean_personas.py                 # 기본 5,000명
    python scripts/fetch_korean_personas.py --count 20000 --seed 7
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DATASET = "nvidia/Nemotron-Personas-Korea"
ROWS_URL = "https://datasets-server.huggingface.co/rows"
TOTAL_ROWS = 1_000_000
PAGE_SIZE = 100  # datasets-server 최대값

# 시뮬레이션에 쓰는 컬럼만 저장한다
KEEP_COLUMNS = [
    "uuid", "persona", "professional_persona", "family_persona", "culinary_persona",
    "cultural_background", "skills_and_expertise", "hobbies_and_interests_list",
    "career_goals_and_ambitions", "sex", "age", "marital_status", "family_type",
    "housing_type", "education_level", "occupation", "district", "province",
]

DEFAULT_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "korean_personas.jsonl")


def fetch_page(offset: int, retries: int = 4) -> list:
    query = urllib.parse.urlencode({
        "dataset": DATASET, "config": "default", "split": "train",
        "offset": offset, "length": PAGE_SIZE,
    })
    delay = 2.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(f"{ROWS_URL}?{query}", timeout=60) as response:
                payload = json.load(response)
            return [item["row"] for item in payload.get("rows", [])]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries - 1:
                raise
            print(f"  offset {offset} 재시도 ({exc})", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=5000, help="저장할 페르소나 수（100 단위로 올림）")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    pages = max(1, -(-args.count // PAGE_SIZE))
    rng = random.Random(args.seed)
    offsets = sorted(rng.sample(range(0, TOTAL_ROWS - PAGE_SIZE, PAGE_SIZE), pages))

    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    tmp_path = f"{output}.tmp"
    written = 0
    with open(tmp_path, "w", encoding="utf-8") as f:
        for index, offset in enumerate(offsets, 1):
            for row in fetch_page(offset):
                f.write(json.dumps({key: row.get(key) for key in KEEP_COLUMNS}, ensure_ascii=False) + "\n")
                written += 1
            print(f"[{index}/{pages}] {written}명 저장", end="\r")
    os.replace(tmp_path, output)
    print(f"\n완료: {written}명 → {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
