#!/usr/bin/env python3

import argparse
import os
import subprocess
import tempfile
import sys
import re
import urllib.parse
from datetime import datetime, timedelta

html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NIPA {branch2} info</title>
    <style>
        body {{
            font-family: "roboto mono", helvetica, nunito, sans-serif;
            margin: 0;
            padding: 20px;
            color: #333;
            background-color: #fff;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1, h2 {{
            color: #444;
        }}
        .header {{
            background-color: #f5f5f5;
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}
        .section {{
            margin-bottom: 30px;
            border: 1px solid #ddd;
            border-radius: 5px;
            overflow: hidden;
        }}
        .section-header {{
            background-color: #eee;
            padding: 10px;
            font-weight: bold;
            border-bottom: 1px solid #ddd;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .controls {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .toggle-button {{
            background-color: #0366d6;
            border: 1px solid #0366d6;
            border-radius: 3px;
            padding: 5px 10px;
            cursor: pointer;
            font-size: 0.9em;
            color: white;
            transition: all 0.2s ease;
        }}
        .toggle-button:hover {{
            background-color: #0056b3;
        }}
        .toggle-button:active {{
            background-color: #004494;
        }}
        .hidden {{
            display: none;
        }}
        .section-content {{
            padding: 10px;
            white-space: pre-wrap;
            font-family: monospace;
        }}
        .diff-add {{
            background-color: #e6ffed;
            color: #22863a;
        }}
        .diff-del {{
            background-color: #ffeef0;
            color: #cb2431;
        }}
        .diff-commit {{
            background-color: #f9fbff;
            color: #273f5c;
        }}
        .diff-info {{
            color: #6a737d;
        }}
        .timestamp {{
            color: #666;
            font-size: 0.9em;
            margin-top: 10px;
        }}
        a {{
            color: #0366d6;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}

        /* Dark mode support */
        @media (prefers-color-scheme: dark) {{
            body {{
                color: #b8b8b8;
                background-color: #1c1c1c;
            }}
            h1, h2 {{
                color: #d0d0d0;
            }}
            a {{
                color: #809fff;
            }}
            .header {{
                background-color: #282828;
                border-color: #181818;
            }}
            .section {{
                border-color: #181818;
            }}
            .section-header {{
                background-color: #303030;
                border-color: #181818;
            }}
            .section-content {{
                background-color: #282828;
            }}
            .toggle-button {{
                background-color: #2c5282;
                border-color: #2c5282;
                color: #e2e8f0;
            }}
            .toggle-button:hover {{
                background-color: #2b4c7e;
            }}
            .toggle-button:active {{
                background-color: #1e3a5f;
            }}
            .diff-add {{
                background-color: #0f3a1e;
                color: #7ce38b;
            }}
            .diff-del {{
                background-color: #3c1618;
                color: #f9a8a8;
            }}
            .diff-commit {{
                background-color: #1b1f26;
                color: #a8b8ca;
            }}
            .diff-info {{
                color: #8b949e;
            }}
            .diff-unchanged {{
                /* No special styling for unchanged lines by default */
            }}
            .timestamp {{
                color: #8b949e;
            }}
        }}
    </style>

    <script>
        document.addEventListener('DOMContentLoaded', function() {{
            const toggleButton = document.getElementById('toggle-unchanged');
            const diffContent = document.getElementById('commit-diff-content');
            let unchangedHidden = false;

            // Function to update button text with count of hidden lines
            function updateButtonText() {{
                const unchangedLines = diffContent.querySelectorAll('.diff-unchanged');
                const hiddenCount = unchangedHidden ? unchangedLines.length : 0;

                if (unchangedHidden) {{
                    toggleButton.textContent = `Show commits present in both (${{hiddenCount}} hidden)`;
                }} else {{
                    toggleButton.textContent = 'Hide commits present in both';
                }}
            }}

            // Function to toggle visibility of unchanged lines
            function toggleUnchangedLines() {{
                const unchangedLines = diffContent.querySelectorAll('.diff-unchanged');

                if (unchangedHidden) {{
                    // Show unchanged lines
                    unchangedLines.forEach(line => {{
                        line.classList.remove('hidden');
                    }});
                    unchangedHidden = false;
                }} else {{
                    // Hide unchanged lines
                    unchangedLines.forEach(line => {{
                        line.classList.add('hidden');
                    }});
                    unchangedHidden = true;
                }}

                updateButtonText();
            }}

            // Hide unchanged lines by default on page load
            toggleUnchangedLines();

            // Add click event listener to toggle button
            toggleButton.addEventListener('click', toggleUnchangedLines);

            // Check if Next button link is dead and hide it if so
            const nextUrl = '{next_url}';
            const nextButton = document.getElementById('next-button');
            if (nextUrl && nextButton) {{
                fetch(nextUrl, {{ method: 'HEAD' }})
                    .then(response => {{
                        if (!response.ok) {{
                            nextButton.style.display = 'none';
                        }}
                    }})
                    .catch(() => {{
                        // If fetch fails (e.g., network error, 404), hide the button
                        nextButton.style.display = 'none';
                    }});
            }}
        }});
    </script>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.4/jquery.min.js"></script>
    <script src="/nipa.js"></script>
    <script>
        nipa_load_sitemap();
    </script>
</head>
<body>
    <div id="sitemap"></div>
    <div class="container">
        <h1>NIPA Branch {branch2}</h1>
        <div class="section">
            <div class="section-header">
                <span>Branches</span>
                <div class="controls">
                    <button class="toggle-button" onclick="window.location.href='{prev_url}'">Previous</button>
                    <button class="toggle-button" onclick="window.location.href='{next_url}'" id="next-button">Next</button>
                </div>
            </div>
            <div class="section-content">   {branch2_html} (current)\n   {branch1_html} (comparison){compare_link}</div>
        </div>

        <div class="section">
            <div class="section-header">Base trees</div>
            <div class="section-content">{ancestor_info}\n{base_diff}</div>
        </div>

        <div class="section">
            <div class="section-header">
                <span>New patches</span>
                <div class="controls">
                    <button id="toggle-unchanged" class="toggle-button">Show all patches</button>
                </div>
            </div>
            <div class="section-content" id="commit-diff-content">{commit_diff}</div>
        </div>

        <div class="section">
            <div class="section-header">
                <span>Test results</span>
            </div>
            <div>
                <iframe src="https://netdev.bots.linux.dev/contest.html?branch={branch2_encoded}&pass=0&pw-n=0&embed=1"
                        width="100%" height="600px" frameborder="0"></iframe>
            </div>
        </div>
    </div>
</body>
</html>
"""


def parse_branch_datetime(branch_name):
    """Extract date and time from branch name format like 'net-next-2025-06-28--21-00'."""
    match = re.search(r'(.*?)(\d{4}-\d{2}-\d{2})--(\d{2})-(\d{2})', branch_name)
    if match:
        date_str = match.group(2)
        hour_str = match.group(3)
        minute_str = match.group(4)
        try:
            return match.group(1), datetime.strptime(f"{date_str} {hour_str}:{minute_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None, None
    return None, None

def generate_next_branch_name(branch1, branch2):
    """Generate the next branch name based on the time difference between branch1 and branch2."""
    # Parse datetime from branch names
    prefix, dt1 = parse_branch_datetime(branch1)
    prefix, dt2 = parse_branch_datetime(branch2)
    if not prefix or not dt1 or not dt2:
        return None

    # Calculate time difference
    time_diff = dt2 - dt1
    next_dt = dt2 + time_diff
    return f"{prefix}{next_dt.strftime('%Y-%m-%d--%H-%M')}"

# Format branch names for display and file paths
def branch_name_clear(name):
    if not name:
        return None
    name = name.strip()
    if name.startswith('remotes/') and name.count('/') >= 2:
        name = "/".join(name.split('/')[2:])
    return name

def generate_html(args, branch1, branch2, base_diff_output, commit_diff_output,
                  ancestor_info=None, committed=None):
    """Generate HTML output for the diff."""
    # Generate next branch name
    branch1 = branch_name_clear(branch1)
    branch2 = branch_name_clear(branch2)
    next_branch = generate_next_branch_name(branch1, branch2)

    # URL encode branch2 for the contest results iframe
    branch2_encoded = urllib.parse.quote(branch2)

    # Process diff output to add HTML styling
    def process_diff(diff_text):
        if not diff_text:
            return "<p>No differences found.</p>"

        lines = []
        for line in diff_text.split('\n'):
            if line.startswith('---') or line.startswith('+++') or line.startswith('index') or line.startswith('diff --git'):
                pass
            elif line.startswith('+') and not line.startswith('+++'):
                lines.append(f'<div class="diff-add">[+] {line[1:]}</div>')
            elif line.startswith('-') and not line.startswith('---'):
                title = line[1:]
                if title in committed:
                    lines.append(f'<div class="diff-commit">[c] {title}</div>')
                else:
                    lines.append(f'<div class="diff-del">[-] {line[1:]}</div>')
            elif line.startswith('@@'):
                lines.append(f'<div class="diff-info">{line}</div>')
            else:
                lines.append(f'<div class="diff-unchanged">   {line}</div>')

        return ''.join(lines)

    # Process the diff outputs
    processed_ancestor_info = process_diff(ancestor_info)
    processed_commit_diff = process_diff(commit_diff_output)
    compare_link = ""

    github_url = args.github_url
    if github_url:
        # Remove trailing slash if present
        if github_url.endswith('/'):
            github_url = github_url[:-1]

        compare_link = f'<div style="margin-top: 10px;"><a href="{github_url}/compare/{branch1}...{branch2}#files_bucket" target="_blank">Compare code</a></div>'

        branch1_html = f'<a href="{github_url}/commits/{branch1}" target="_blank">{branch1}</a>'
        branch2_html = f'<a href="{github_url}/commits/{branch2}" target="_blank">{branch2}</a>'
    else:
        branch1_html = branch1
        branch2_html = branch2
        compare_link = ""

    # Generate the HTML
    html = html_template.format(
        branch1=branch1,
        branch2=branch2,
        branch1_html=branch1_html,
        branch2_html=branch2_html,
        compare_link=compare_link,
        ancestor_info=processed_ancestor_info,
        base_diff=base_diff_output,
        commit_diff=processed_commit_diff,
        prev_url=f"{branch1}.html",
        next_url=f"{next_branch}.html" if next_branch else '',
        branch2_encoded=branch2_encoded
    )

    return html

def text_print(args: argparse.Namespace, message: str) -> None:
    """Print message to stdout only if HTML output is not requested."""
    if not args.html:
        print(message)

def run_command(cmd):
    """Run a shell command and return its output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()

def get_base(branch):
    """Get the base commit for a branch."""
    cmd = f"git log -1 --format='%h' --grep=\"Merge git://git.kernel.org/pub/scm/linux/kernel/git/netdev/net\" {branch}"
    return run_command(cmd)

def get_common_ancestor(commit1, commit2):
    """Find the common ancestor of two commits."""
    cmd = f"git merge-base {commit1} {commit2}"
    return run_command(cmd)

def get_commit_list(start_commit, end_commit):
    """Get a list of commits between start_commit and end_commit."""
    cmd = f"git log --format='%h#%s' {start_commit}..{end_commit}"
    commits = run_command(cmd)
    # Skip the first line, it's the net/main merge commit
    return [x.split("#") for x in reversed(commits.split('\n')[1:])]

def get_base_diff(base1, base2):
    """Get the diff between two base commits."""
    # Find common ancestor between the base commits
    common_ancestor = get_common_ancestor(base1, base2)

    # Get commit lists between common ancestor and base commits
    commits1 = get_commit_list(common_ancestor, base1)
    commits2 = get_commit_list(common_ancestor, base2)

    committed = set()
    diff_list = []

    set1 = set([x for x, _ in commits1])
    set2 = set([x for x, _ in commits2])
    for h, s in commits1:
        if h not in set2:
            diff_list.append("-" + s)
    for h, s in commits2:
        if h not in set1:
            diff_list.append("+" + s)
            committed.add(s)
    return "\n".join(diff_list), committed

def main():
    parser = argparse.ArgumentParser(description='Compare two git branches.')
    parser.add_argument('branch1', nargs='?', default=None, help='First branch to compare')
    parser.add_argument('branch2', nargs='?', default=None, help='Second branch to compare')
    parser.add_argument('--html', '-H', action='store_true', help='Generate HTML output')
    parser.add_argument('--output', '-o', help='Output file for HTML (default: cidiff_result.html)')
    parser.add_argument('--github-url', '-g', help='GitHub repository URL (to create branch links in HTML output)')
    args = parser.parse_args()

    branch1 = args.branch1
    branch2 = args.branch2

    # Determine which branches to compare
    if not branch1 and not branch2:
        text_print(args, "No branches specified, using two most recent:")
        branches = run_command("git branch -a | tail -2").split('\n')
        branch1 = branches[0].strip()
        branch2 = branches[1].strip() if len(branches) > 1 else None
    elif branch1 and not branch2:
        text_print(args, "Single branch specified, using that and the previous one:")
        branches = run_command(f"git branch -a | grep -B1 \"{branch1}\"").split('\n')
        branch1 = branches[0].strip()
        branch2 = branches[1].strip() if len(branches) > 1 else None

    if not branch2:
        print("Error: Could not determine second branch.")
        sys.exit(1)

    text_print(args, f"   {branch1} ({run_command(f'git describe {branch1}')})")
    text_print(args, f"   {branch2} ({run_command(f'git describe {branch2}')})")
    text_print(args, "")

    # Get base commits
    base1 = get_base(branch1)
    base2 = get_base(branch2)

    # Compare base commits
    result = subprocess.run(f"git diff --exit-code --stat {base1} {base2}",
                           shell=True, capture_output=True, text=True)

    base_diff_output = ""
    base_diff_status = ""
    if result.returncode == 0:
        base_diff_status = "==== BASE IDENTICAL ===="
        base_diff_list, committed = "", set()
    else:
        base_diff_status = "==== BASE DIFF ===="
        base_diff_output = run_command(f"git --no-pager diff --stat {base1} {base2}")
        base_diff_list, committed = get_base_diff(base1, base2)

    text_print(args, base_diff_status)
    if base_diff_output:
        text_print(args, base_diff_output)
        text_print(args, "")
        text_print(args, base_diff_list + "\n")

    # Create temporary files with commit messages
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as tmp1, \
         tempfile.NamedTemporaryFile(mode='w+', delete=False) as tmp2:

        tmp1_path = tmp1.name
        tmp2_path = tmp2.name

        tmp1.write(run_command(f"git log --format=\"%s\" {base1}..{branch1}"))
        tmp2.write(run_command(f"git log --format=\"%s\" {base2}..{branch2}"))
        tmp1.write("\n")
        tmp2.write("\n")

    # Compare commit lists
    if not args.html:
        print("==== COMMIT DIFF ====")
        subprocess.run(f"git --no-pager diff --no-index {tmp1_path} {tmp2_path}", shell=True)
    else:
        commit_diff_result = subprocess.run(
            f"git --no-pager diff -U500 --no-index {tmp1_path} {tmp2_path}",
            shell=True, capture_output=True, text=True
        )
        commit_diff_output = commit_diff_result.stdout if commit_diff_result.stdout else commit_diff_result.stderr

        html_output = generate_html(args, branch1, branch2, base_diff_output,
                                    commit_diff_output,
                                    base_diff_list, committed)
        if args.output:
            with open(args.output, 'w') as f:
                f.write(html_output)
            print(f"HTML output written to {args.output}")
        else:
            print(html_output)

    # Clean up temporary files
    os.unlink(tmp1_path)
    os.unlink(tmp2_path)

if __name__ == "__main__":
    main()
