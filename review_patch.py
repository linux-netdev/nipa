#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

import argparse
import configparser
import json
import os

import boto3

import requests
from core import log, log_end_sec, log_init, log_open_sec
from pw import Patchwork


# THIS WILL ONLY WORK IN EC2 INSTANCE ENVIRONMENT
def review_patch_with_bedrock(title, commit_msg, diff):
    bedrock = boto3.client("bedrock-runtime")

    prompt = f"""Review this Linux kernel patch:

                Title: {title}

                Commit Message:
                {commit_msg}

                Code Changes:
                {diff}

                Please review for:
                1. Correctness and bugs
                2. Linux kernel coding style
                3. Memory safety
                4. Security concerns
                5. Performance implications
                """

    body_dict = {
        "anthropic_version": os.environ.get("BEDROCK_ANTHROPIC_VERSION"),
        "max_tokens": int(os.environ.get("BEDROCK_MAX_TOKENS")),
        "messages": [{"role": "user", "content": prompt}],
    }

    body_bytes = json.dumps(body_dict).encode("utf-8")

    response = bedrock.invoke_model(
        modelId=os.environ.get("ANTHROPIC_MODEL"),
        body=body_bytes,
    )

    response_body = json.loads(response["body"].read())

    return response_body["content"][0]["text"]


def main():
    parser = argparse.ArgumentParser(
        description="AI Code Reviewer for Patchwork",
        usage="%(prog)s --series SERIES_ID [--output OUTPUT]\n\n"
        "Examples:\n"
        "  %(prog)s --series 1016234\n"
        "  %(prog)s --series 1016234 --output series.html",
    )
    parser.add_argument(
        "--series", "-s", required=True, type=int, help="Series ID to fetch"
    )
    parser.add_argument("--output", "-o", help="Output file (default: print to stdout)")

    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(["nipa.config", "pw.config", "review.config"])

    log_init(
        config.get("log", "type", fallback="stdout"),
        config.get("log", "file", fallback=None),
    )

    pw = Patchwork(config)

    series = pw.get("series", args.series)
    log_open_sec(f"Logging series {args.series} data")
    log("Series information", series)
    log_end_sec()

    for patch in series["patches"]:
        log_open_sec(f'Processing patch id {patch["id"]}')

        page = requests.get(patch["url"]).json()

        title = page["name"]
        commit_msg = page["content"]
        diff = page["diff"]

        log_open_sec(f"Title")
        log(title)
        log_end_sec()

        log_open_sec(f"Content")
        log(commit_msg)
        log_end_sec()

        log_open_sec(f"Diff")
        log(diff)
        log_end_sec()

        log_open_sec(f"Patch Review")
        log(review_patch_with_bedrock(title, commit_msg, diff))
        log_end_sec()

        log_end_sec()


if __name__ == "__main__":
    main()
