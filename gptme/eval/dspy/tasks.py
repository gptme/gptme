"""
Specialized evaluation tasks for prompt optimization.

These tasks are designed to test specific aspects of system prompts
including tool usage, reasoning, instruction following, and problem solving.
"""

from typing import Any


def create_tool_usage_tasks() -> list[dict[str, Any]]:
    """
    Create tasks that specifically test tool usage patterns.

    These tasks evaluate whether the system prompt leads to appropriate
    tool selection and usage patterns.
    """
    return [
        {
            "name": "tool-save-simple",
            "files": {},
            "run": "python hello.py",
            "prompt": 'Create a Python file called hello.py that prints "Hello, World!"',
            "tools": ["save"],
            "expect": {
                "uses_save_tool": lambda ctx: "hello.py" in ctx.files,
                "correct_output": lambda ctx: "Hello, World!" in ctx.stdout,
            },
            "focus_areas": ["tool_selection", "file_creation"],
        },
        {
            "name": "tool-patch-vs-save",
            "files": {"greeting.py": 'print("Hi there")'},
            "run": "python greeting.py",
            "prompt": 'Change the greeting.py file to print "Hello, World!" instead',
            "tools": ["save", "patch"],
            "expect": {
                "prefers_patch": lambda ctx: "patch" in str(ctx.messages)
                and "save" not in str(ctx.messages),
                "correct_output": lambda ctx: "Hello, World!" in ctx.stdout,
            },
            "focus_areas": ["tool_selection", "incremental_changes"],
        },
        {
            "name": "tool-read-before-modify",
            "files": {"config.py": "DEBUG = True\nVERSION = '1.0'"},
            "run": "python -c 'import config; print(config.VERSION)'",
            "prompt": 'Update the VERSION in config.py to "2.0" but keep DEBUG setting unchanged',
            "tools": ["read", "patch", "save"],
            "expect": {
                "reads_first": lambda ctx: any(
                    "read" in str(msg) for msg in ctx.messages[:3]
                ),
                "correct_output": lambda ctx: "2.0" in ctx.stdout,
                "preserves_debug": lambda ctx: "DEBUG = True"
                in ctx.files.get("config.py", ""),
            },
            "focus_areas": ["tool_sequencing", "context_gathering"],
        },
        {
            "name": "tool-shell-exploration",
            "files": {},
            "run": "ls -la",
            "prompt": "List all files in the current directory including hidden files",
            "tools": ["shell"],
            "expect": {
                "uses_shell": lambda ctx: "shell" in str(ctx.messages),
                "correct_command": lambda ctx: "ls -la" in str(ctx.messages)
                or "ls -a" in str(ctx.messages),
            },
            "focus_areas": ["command_generation", "shell_usage"],
        },
    ]


def create_reasoning_tasks() -> list[dict[str, Any]]:
    """
    Create tasks that test reasoning and problem-solving capabilities.

    These tasks evaluate whether the system prompt encourages good
    problem-solving approaches and step-by-step thinking.
    """
    return [
        {
            "name": "reasoning-debug-error",
            "files": {"broken.py": "print('Hello World')\nprint(undeclared_variable)"},
            "run": "python broken.py",
            "prompt": "Fix the error in broken.py and make it run successfully",
            "tools": ["shell", "patch", "read"],
            "expect": {
                "identifies_error": lambda ctx: "undeclared_variable"
                in str(ctx.messages).lower()
                or "not defined" in str(ctx.messages).lower(),
                "fixes_error": lambda ctx: ctx.return_code == 0,
                "explains_fix": lambda ctx: len(
                    [
                        m
                        for m in ctx.messages
                        if m.role == "assistant" and len(m.content) > 50
                    ]
                )
                > 0,
            },
            "focus_areas": ["error_analysis", "debugging", "explanation_quality"],
        },
        {
            "name": "reasoning-multi-step",
            "files": {},
            "run": "python fibonacci.py",
            "prompt": "Create a program that calculates and prints the first 10 Fibonacci numbers",
            "tools": ["save", "shell"],
            "expect": {
                "shows_planning": lambda ctx: any(
                    "step" in str(msg).lower() or "first" in str(msg).lower()
                    for msg in ctx.messages
                    if msg.role == "assistant"
                ),
                "correct_output": lambda ctx: "0, 1, 1, 2, 3, 5, 8, 13, 21, 34"
                in ctx.stdout.replace("\n", ", "),
                "tests_solution": lambda ctx: any(
                    "run" in str(msg) or "test" in str(msg) for msg in ctx.messages
                ),
            },
            "focus_areas": [
                "problem_decomposition",
                "verification",
                "algorithmic_thinking",
            ],
        },
        {
            "name": "reasoning-requirements-analysis",
            "files": {},
            "run": "python calculator.py",
            "prompt": "Build a calculator that can add, subtract, multiply, and divide two numbers provided as command line arguments",
            "tools": ["save", "shell"],
            "expect": {
                "analyzes_requirements": lambda ctx: any(
                    "command line" in str(msg).lower()
                    for msg in ctx.messages
                    if msg.role == "assistant"
                ),
                "handles_args": lambda ctx: "sys.argv"
                in ctx.files.get("calculator.py", "")
                or "argparse" in ctx.files.get("calculator.py", ""),
                "implements_operations": lambda ctx: all(
                    op in ctx.files.get("calculator.py", "")
                    for op in ["+", "-", "*", "/"]
                ),
            },
            "focus_areas": [
                "requirements_understanding",
                "comprehensive_implementation",
            ],
        },
    ]


def create_instruction_following_tasks() -> list[dict[str, Any]]:
    """
    Create tasks that test adherence to specific instructions and constraints.

    These tasks evaluate whether the system prompt helps follow detailed
    instructions and respect constraints.
    """
    return [
        {
            "name": "instruction-no-placeholders",
            "files": {},
            "run": "bash deploy.sh",
            "prompt": "Create a deployment script deploy.sh that echoes 'Deploying to production'. Do not use any placeholder variables.",
            "tools": ["save"],
            "expect": {
                "no_placeholders": lambda ctx: "$" not in ctx.files.get("deploy.sh", "")
                and "placeholder" not in ctx.files.get("deploy.sh", "").lower(),
                "correct_output": lambda ctx: "Deploying to production" in ctx.stdout,
                "executable": lambda ctx: ctx.return_code == 0,
            },
            "focus_areas": ["constraint_following", "avoiding_placeholders"],
        },
        {
            "name": "instruction-absolute-paths",
            "files": {},
            "run": "python path_example.py",
            "prompt": "Create a Python script that prints the absolute path of the current working directory. Use absolute paths in your solution.",
            "tools": ["save", "shell"],
            "expect": {
                "uses_absolute_paths": lambda ctx: any(
                    "/" in str(msg) and not msg.content.startswith("```")
                    for msg in ctx.messages
                    if msg.role == "assistant"
                ),
                "correct_logic": lambda ctx: "os.getcwd()"
                in ctx.files.get("path_example.py", "")
                or "pathlib" in ctx.files.get("path_example.py", ""),
            },
            "focus_areas": ["path_handling", "instruction_adherence"],
        },
        {
            "name": "instruction-preserve-comments",
            "files": {
                "commented.py": "# Important configuration\nDEBUG = True  # Keep this for development\n# Production settings\nPORT = 8080"
            },
            "run": "python -c 'import commented; print(commented.PORT)'",
            "prompt": "Change the PORT to 3000 in commented.py but preserve all existing comments",
            "tools": ["patch", "read"],
            "expect": {
                "preserves_comments": lambda ctx: "# Important configuration"
                in ctx.files.get("commented.py", "")
                and "# Production settings" in ctx.files.get("commented.py", ""),
                "changes_port": lambda ctx: "PORT = 3000"
                in ctx.files.get("commented.py", ""),
                "correct_output": lambda ctx: "3000" in ctx.stdout,
            },
            "focus_areas": ["comment_preservation", "selective_modification"],
        },
    ]


def create_error_handling_tasks() -> list[dict[str, Any]]:
    """
    Create tasks that test error handling and recovery capabilities.

    These tasks evaluate whether the system prompt leads to good
    error handling and self-correction behavior.
    """
    return [
        {
            "name": "error-command-failure",
            "files": {},
            "run": "python test_error.py",
            "prompt": "Run the command 'nonexistent_command --help' and then create a Python script that prints 'Command not found' when that happens",
            "tools": ["shell", "save"],
            "expect": {
                "attempts_command": lambda ctx: "nonexistent_command"
                in str(ctx.messages),
                "handles_failure": lambda ctx: "not found" in str(ctx.messages).lower()
                or "error" in str(ctx.messages).lower(),
                "creates_alternative": lambda ctx: "test_error.py" in ctx.files,
                "correct_output": lambda ctx: "Command not found" in ctx.stdout,
            },
            "focus_areas": ["error_recovery", "alternative_solutions"],
        },
        {
            "name": "error-syntax-correction",
            "files": {
                "broken_syntax.py": "def greet(name\n    print(f'Hello, {name}!')\n\ngreet('World')"
            },
            "run": "python broken_syntax.py",
            "prompt": "Fix the syntax errors in broken_syntax.py and make it run correctly",
            "tools": ["shell", "patch", "read"],
            "expect": {
                "identifies_syntax_errors": lambda ctx: "syntax"
                in str(ctx.messages).lower(),
                "fixes_missing_paren": lambda ctx: "def greet(name):"
                in ctx.files.get("broken_syntax.py", ""),
                "fixes_indentation": lambda ctx: "    print("
                in ctx.files.get("broken_syntax.py", ""),
                "correct_output": lambda ctx: "Hello, World!" in ctx.stdout,
            },
            "focus_areas": ["syntax_error_handling", "code_correction"],
        },
    ]


def get_prompt_optimization_tasks() -> list[dict[str, Any]]:
    """
    Get all tasks designed for prompt optimization.

    Returns a comprehensive set of tasks that test different aspects
    of system prompt effectiveness.
    """
    all_tasks = []
    all_tasks.extend(create_tool_usage_tasks())
    all_tasks.extend(create_reasoning_tasks())
    all_tasks.extend(create_instruction_following_tasks())
    all_tasks.extend(create_error_handling_tasks())
    all_tasks.extend(create_multistep_debugging_tasks())

    return all_tasks


def get_tasks_by_focus_area(focus_area: str) -> list[dict[str, Any]]:
    """
    Get tasks that focus on a specific area.

    Args:
        focus_area: The area to focus on (e.g., "tool_selection", "reasoning")

    Returns:
        List of tasks that test the specified focus area
    """
    all_tasks = get_prompt_optimization_tasks()

    matching_tasks = []
    for task in all_tasks:
        focus_areas = task.get("focus_areas", [])
        if focus_area in focus_areas:
            matching_tasks.append(task)

    return matching_tasks


def analyze_task_coverage() -> dict[str, list[str]]:
    """
    Analyze what focus areas are covered by the available tasks.

    Returns:
        Dictionary mapping focus areas to task names that cover them
    """
    all_tasks = get_prompt_optimization_tasks()
    coverage: dict[str, list[str]] = {}

    for task in all_tasks:
        focus_areas = task.get("focus_areas", [])
        task_name = task.get("name", "unknown")

        for area in focus_areas:
            if area not in coverage:
                coverage[area] = []
            coverage[area].append(task_name)

    return coverage


def create_multistep_debugging_tasks() -> list[dict[str, Any]]:
    """
    Create multi-step debugging tasks for GEPA trajectory optimization.

    These tasks test systematic debugging across interconnected files,
    requiring analysis → trace → fix → verify trajectories.
    """
    return [
        {
            "name": "debug-calculator-project",
            "files": {
                # Main application with syntax error and import issue
                "calculator.py": '''#!/usr/bin/env python3
"""
Simple calculator application with file I/O
"""
import sys
from utility import calculate_average  # Wrong import name - should be 'utils'

def main()  # Missing colon - syntax error
    if len(sys.argv) < 2:
        print("Usage: python calculator.py <numbers_file>")
        sys.exit(1)

    filename = sys.argv[1]
    try:
        with open(filename, 'r') as f:
            numbers = [float(line.strip()) for line in f if line.strip()]

        if not numbers:
            print("No valid numbers found in file")
            return

        # Calculate statistics
        total = sum(numbers)
        avg = calculate_average(numbers)
        maximum = max(numbers)
        minimum = min(numbers)

        print(f"Numbers processed: {len(numbers)}")
        print(f"Total: {total}")
        print(f"Average: {avg}")
        print(f"Maximum: {maximum}")
        print(f"Minimum: {minimum}")

    except FileNotFoundError:
        print(f"Error: File '{filename}' not found")
    except ValueError as e:
        print(f"Error processing numbers: {e}")

if __name__ == "__main__":
    main()
''',
                # Utility module with logic bug
                "utils.py": '''"""
Utility functions for mathematical operations
"""

def calculate_average(numbers):
    """Calculate average of a list of numbers"""
    if not numbers:
        return 0
    # Logic bug: should divide by len(numbers), not len(numbers) + 1
    return sum(numbers) / (len(numbers) + 1)

def calculate_median(numbers):
    """Calculate median of a list of numbers"""
    if not numbers:
        return 0

    sorted_nums = sorted(numbers)
    n = len(sorted_nums)

    if n % 2 == 0:
        return (sorted_nums[n//2 - 1] + sorted_nums[n//2]) / 2
    else:
        return sorted_nums[n//2]

def find_outliers(numbers, threshold=2.0):
    """Find outliers using simple standard deviation method"""
    if len(numbers) < 3:
        return []

    mean = sum(numbers) / len(numbers)
    variance = sum((x - mean) ** 2 for x in numbers) / len(numbers)
    std_dev = variance ** 0.5

    outliers = []
    for num in numbers:
        if abs(num - mean) > threshold * std_dev:
            outliers.append(num)

    return outliers
''',
                # Test data file
                "test_numbers.txt": """10.5
20.0
15.3
18.7
12.9
25.1
30.8
14.2
16.5
22.3
""",
                # Expected output for verification
                "expected_output.txt": """Numbers processed: 10
Total: 186.3
Average: 18.63
Maximum: 30.8
Minimum: 10.5
""",
            },
            "run": "python calculator.py test_numbers.txt",
            "prompt": (
                "This Python calculator project has multiple bugs preventing it from running correctly. "
                "Please analyze the code, identify all issues, and fix them systematically. "
                "The program should read numbers from a file and calculate basic statistics. "
                "Test your fixes by running the calculator with the provided test_numbers.txt file."
            ),
            "tools": ["read", "patch", "shell", "save"],
            "expect": {
                "fixes_syntax_error": lambda ctx: "def main():"
                in ctx.files.get("calculator.py", ""),
                "fixes_import_error": lambda ctx: "from utils import"
                in ctx.files.get("calculator.py", ""),
                "fixes_logic_bug": lambda ctx: "len(numbers) + 1"
                not in ctx.files.get("utils.py", ""),
                "produces_correct_output": lambda ctx: "Average: 18.63" in ctx.stdout,
                "successful_execution": lambda ctx: ctx.returncode == 0,
            },
            "focus_areas": [
                "multi_step_debugging",
                "systematic_analysis",
                "error_tracing",
                "file_coordination",
            ],
            "expected_trajectory": [
                "Read and analyze all project files to understand structure",
                "Attempt to run the program to identify runtime errors",
                "Fix syntax error in main function definition",
                "Fix import error by correcting module name",
                "Run again to identify logic errors",
                "Fix calculation bug in average function",
                "Verify the complete fix by running with test data",
            ],
        },
        {
            "name": "debug-web-scraper",
            "files": {
                # Main scraper with multiple issues
                "scraper.py": '''import requests
from bs4 import BeautifulSoup
import json
from urllib.parse import urljoin
import time

# Missing import for 'os' module
from config import HEADERS, DELAY_SECONDS

def fetch_page(url):
    """Fetch a web page with error handling"""
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

def parse_articles(html):
    """Parse articles from HTML content"""
    soup = BeautifulSoup(html, 'html.parser')
    articles = []

    # Logic bug: should look for 'article' tags, not 'div' tags
    for item in soup.find_all('div', class_='article'):
        title_elem = item.find('h2')
        link_elem = item.find('a')

        if title_elem and link_elem:
            article = {
                'title': title_elem.text.strip(),
                'link': urljoin('https://example.com', link_elem['href']),
                'timestamp': time.time()  # Should use actual publish date
            }
            articles.append(article)

    return articles

def save_articles(articles, filename):
    """Save articles to JSON file"""
    # Missing error handling for file operations
    with open(filename, 'w') as f:
        json.dump(articles, f, indent=2)
    print(f"Saved {len(articles)} articles to {filename}")

def main():
    url = "https://example.com/news"

    print(f"Fetching articles from {url}...")
    html = fetch_page(url)

    if html:
        articles = parse_articles(html)

        if articles:
            # Bug: trying to use undefined variable
            output_file = os.path.join('data', 'articles.json')
            save_articles(articles, output_file)
        else:
            print("No articles found")
    else:
        print("Failed to fetch page")

    time.sleep(DELAY_SECONDS)

if __name__ == "__main__":
    main()
''',
                # Configuration with issues
                "config.py": '''# Web scraper configuration

# Missing import for random module needed for user agent rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
]

def get_random_user_agent():
    """Get a random user agent string"""
    return random.choice(USER_AGENTS)  # NameError: random not imported

HEADERS = {
    'User-Agent': get_random_user_agent(),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}

DELAY_SECONDS = 1
''',
                # Test HTML for parsing
                "test_page.html": """<!DOCTYPE html>
<html>
<head><title>Test News Site</title></head>
<body>
    <article class="article">
        <h2>First Article Title</h2>
        <a href="/article/1">Read more</a>
    </article>
    <article class="article">
        <h2>Second Article Title</h2>
        <a href="/article/2">Read more</a>
    </article>
    <div class="sidebar">Not an article</div>
</body>
</html>
""",
            },
            "run": "python -c \"import scraper; print('Import successful')\"",
            "prompt": (
                "This web scraper has several bugs that prevent it from running. "
                "Analyze the code to identify import errors, logic bugs, and missing dependencies. "
                "Fix the issues so the scraper can be imported and run without errors. "
                "Focus on making the code functional rather than actually scraping live websites."
            ),
            "tools": ["read", "patch", "shell", "save"],
            "expect": {
                "fixes_missing_imports": lambda ctx: "import os"
                in ctx.files.get("scraper.py", "")
                and "import random" in ctx.files.get("config.py", ""),
                "fixes_selector_logic": lambda ctx: "article"
                in ctx.files.get("scraper.py", "")
                and "div"
                not in ctx.files.get("scraper.py", "")
                .split("find_all(")[1]
                .split(")")[0],
                "successful_import": lambda ctx: "Import successful" in ctx.stdout
                and ctx.returncode == 0,
            },
            "focus_areas": [
                "import_debugging",
                "dependency_analysis",
                "web_scraping",
                "configuration_bugs",
            ],
            "expected_trajectory": [
                "Read all files to understand the scraper architecture",
                "Try to import/run to identify missing imports",
                "Fix missing 'os' import in main scraper",
                "Fix missing 'random' import in config module",
                "Identify and fix logic errors in parsing",
                "Verify fixes by testing import and basic functionality",
            ],
        },
        {
            "name": "research-implement-caching",
            "files": {
                # Basic web service without caching
                "api_server.py": '''#!/usr/bin/env python3
"""
Simple API server that needs caching implementation
"""
import time
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/api/expensive-calculation':
            # Simulate expensive computation
            params = parse_qs(parsed_path.query)
            number = int(params.get('number', [10])[0])

            result = self.expensive_fibonacci(number)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            response = {
                'input': number,
                'result': result,
                'computation_time': 'varies'
            }
            self.wfile.write(json.dumps(response).encode())

        elif parsed_path.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'running'}).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def expensive_fibonacci(self, n):
        """Deliberately inefficient fibonacci calculation"""
        print(f"Computing fibonacci({n})...")
        time.sleep(0.5)  # Simulate network/DB delay

        if n <= 1:
            return n
        return self.expensive_fibonacci(n-1) + self.expensive_fibonacci(n-2)

    def log_message(self, format, *args):
        """Override to reduce noise"""
        pass

def run_server(port=8000):
    """Run the API server"""
    server = HTTPServer(('localhost', port), APIHandler)
    print(f"Server starting on http://localhost:{port}")
    print("Try: http://localhost:8000/api/expensive-calculation?number=10")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.server_close()

if __name__ == "__main__":
    run_server()
''',
                # Basic test client
                "test_client.py": '''#!/usr/bin/env python3
import requests
import time

def test_api_performance():
    """Test API performance with and without caching"""
    base_url = "http://localhost:8000"

    # Test same request multiple times
    test_number = 8
    times = []

    print(f"Testing fibonacci({test_number}) calculation...")
    for i in range(3):
        start_time = time.time()
        response = requests.get(f"{base_url}/api/expensive-calculation?number={test_number}")
        end_time = time.time()

        if response.status_code == 200:
            duration = end_time - start_time
            times.append(duration)
            print(f"Request {i+1}: {duration:.2f} seconds")
        else:
            print(f"Request {i+1}: Failed with status {response.status_code}")

    if times:
        avg_time = sum(times) / len(times)
        print(f"Average response time: {avg_time:.2f} seconds")

        if avg_time > 1.0:
            print("⚠️  Performance issue detected - same calculation taking too long repeatedly")
            print("💡 Consider implementing caching to improve performance")
        else:
            print("✅ Good performance - caching appears to be working")

if __name__ == "__main__":
    test_api_performance()
''',
            },
            "run": "python test_client.py",  # Note: requires server to be running
            "prompt": (
                "This API server has a performance problem - it recalculates expensive operations "
                "every time, even for identical requests. Research different caching strategies "
                "(in-memory, Redis, file-based, etc.) and implement an appropriate caching solution. "
                "The solution should cache results of expensive calculations and serve them quickly "
                "on subsequent requests. Test your implementation to verify performance improvement."
            ),
            "tools": ["read", "patch", "shell", "browser", "save"],
            "expect": {
                "implements_caching": lambda ctx: any(
                    keyword in str(ctx.files.get("api_server.py", "")).lower()
                    for keyword in ["cache", "cached", "lru_cache", "dict"]
                ),
                "research_evidence": lambda ctx: any(
                    keyword in str(ctx.messages).lower()
                    for keyword in [
                        "caching",
                        "cache strategy",
                        "performance",
                        "redis",
                        "memory",
                    ]
                ),
                "performance_test": lambda ctx: "test_client" in str(ctx.messages)
                or "curl" in str(ctx.messages),
            },
            "focus_areas": [
                "web_research",
                "performance_optimization",
                "caching_strategies",
                "api_development",
            ],
            "expected_trajectory": [
                "Research caching strategies and approaches online",
                "Compare different caching options (in-memory, Redis, file-based)",
                "Choose appropriate caching strategy for the use case",
                "Implement caching in the API server code",
                "Test the implementation to verify performance improvement",
                "Validate that cached responses are served quickly",
            ],
        },
        {
            "name": "optimize-data-processing",
            "files": {
                # Inefficient data processing code
                "data_processor.py": '''#!/usr/bin/env python3
"""
Data processing pipeline with performance issues
"""
import csv
import json
import time
from typing import List, Dict, Any

class DataProcessor:
    def __init__(self):
        self.processed_count = 0

    def load_csv_data(self, filename: str) -> List[Dict[str, Any]]:
        """Load CSV data - currently inefficient"""
        print(f"Loading data from {filename}...")
        data = []

        # Inefficiency 1: Reading file multiple times
        with open(filename, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                data.append(dict(row))

        # Inefficiency 2: Processing each row individually
        processed_data = []
        for row in data:
            processed_row = self.process_single_row(row)
            processed_data.append(processed_row)

        return processed_data

    def process_single_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single row - inefficient implementation"""
        # Inefficiency 3: Repeated expensive operations
        result = {}

        # Simulate some processing
        time.sleep(0.01)  # Simulated expensive operation per row

        for key, value in row.items():
            if key == 'amount':
                # Inefficiency 4: Converting strings repeatedly without caching
                try:
                    result['amount_float'] = float(value)
                    result['amount_squared'] = float(value) ** 2
                    result['amount_log'] = self.expensive_log_calculation(float(value))
                except ValueError:
                    result['amount_float'] = 0.0
                    result['amount_squared'] = 0.0
                    result['amount_log'] = 0.0
            else:
                result[key] = value

        self.processed_count += 1
        return result

    def expensive_log_calculation(self, value: float) -> float:
        """Expensive calculation that could be optimized"""
        if value <= 0:
            return 0.0

        # Inefficiency 5: Implementing log manually instead of using math.log
        result = 0.0
        temp = value
        while temp > 1:
            temp /= 2.718281828  # Manual e calculation
            result += 1
        return result

    def filter_data(self, data: List[Dict[str, Any]], min_amount: float) -> List[Dict[str, Any]]:
        """Filter data - inefficient nested loops"""
        filtered = []

        # Inefficiency 6: Nested loops where unnecessary
        for row in data:
            for key, value in row.items():
                if key == 'amount_float' and value >= min_amount:
                    filtered.append(row)
                    break  # Should break after finding match

        return filtered

    def save_results(self, data: List[Dict[str, Any]], filename: str):
        """Save results to JSON"""
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Saved {len(data)} records to {filename}")

def main():
    processor = DataProcessor()

    print("Starting data processing...")
    start_time = time.time()

    # Process sample data
    data = processor.load_csv_data('sample_data.csv')
    filtered_data = processor.filter_data(data, min_amount=100.0)
    processor.save_results(filtered_data, 'processed_results.json')

    end_time = time.time()
    print(f"Processing completed in {end_time - start_time:.2f} seconds")
    print(f"Processed {processor.processed_count} total rows")

if __name__ == "__main__":
    main()
''',
                # Sample data for testing
                "sample_data.csv": """name,amount,category,date
Alice,150.50,food,2024-01-15
Bob,75.25,transport,2024-01-16
Charlie,200.00,utilities,2024-01-17
Diana,45.75,entertainment,2024-01-18
Eve,125.30,food,2024-01-19
Frank,300.00,utilities,2024-01-20
Grace,85.60,transport,2024-01-21
Henry,175.25,food,2024-01-22
Iris,95.40,entertainment,2024-01-23
Jack,250.75,utilities,2024-01-24
""",
                # Performance benchmark script
                "benchmark.py": '''#!/usr/bin/env python3
import time
import subprocess
import sys

def run_benchmark():
    """Run the data processor and measure performance"""
    print("Running performance benchmark...")

    start_time = time.time()
    result = subprocess.run([sys.executable, 'data_processor.py'],
                          capture_output=True, text=True)
    end_time = time.time()

    if result.returncode == 0:
        duration = end_time - start_time
        print(f"Execution time: {duration:.2f} seconds")

        if duration > 2.0:
            print("⚠️  Performance issue: Processing taking too long")
            print("🔍 Recommend profiling and optimization")
        else:
            print("✅ Good performance")

        print("\\nOutput:")
        print(result.stdout)
    else:
        print("❌ Execution failed:")
        print(result.stderr)

if __name__ == "__main__":
    run_benchmark()
''',
            },
            "run": "python benchmark.py",
            "prompt": (
                "This data processing script has multiple performance issues that make it slow and inefficient. "
                "Analyze the code to identify bottlenecks and optimization opportunities. "
                "Implement improvements to make the code faster and more efficient. "
                "Focus on algorithmic improvements, removing redundant operations, and using better data structures. "
                "Measure the performance before and after your optimizations."
            ),
            "tools": ["read", "patch", "shell", "save"],
            "expect": {
                "removes_sleep_delay": lambda ctx: "time.sleep"
                not in ctx.files.get("data_processor.py", ""),
                "optimizes_math_operations": lambda ctx: "math.log"
                in ctx.files.get("data_processor.py", "")
                or "import math" in ctx.files.get("data_processor.py", ""),
                "improves_filtering": lambda ctx: "for key, value in row.items():"
                not in ctx.files.get("data_processor.py", "").split("filter_data")[1]
                if "filter_data" in ctx.files.get("data_processor.py", "")
                else False,
                "measures_performance": lambda ctx: "benchmark"
                in str(ctx.messages).lower()
                or "performance" in str(ctx.messages).lower(),
            },
            "focus_areas": [
                "performance_analysis",
                "code_optimization",
                "algorithmic_improvement",
                "benchmarking",
            ],
            "expected_trajectory": [
                "Analyze the existing code to identify performance bottlenecks",
                "Run initial benchmark to establish baseline performance",
                "Identify specific inefficiencies (sleep delays, redundant operations, poor algorithms)",
                "Implement optimizations systematically (remove delays, improve algorithms, use better data structures)",
                "Test optimizations to ensure correctness is maintained",
                "Measure performance improvements and validate the optimization impact",
            ],
        },
    ]


if __name__ == "__main__":
    # Print task coverage analysis
    print("=== Prompt Optimization Task Coverage ===")
    coverage = analyze_task_coverage()

    for area, tasks in sorted(coverage.items()):
        print(f"\n{area}:")
        for task in tasks:
            print(f"  - {task}")

    print(f"\nTotal tasks: {len(get_prompt_optimization_tasks())}")
    print(f"Focus areas covered: {len(coverage)}")
