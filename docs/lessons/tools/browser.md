---
match:
  keywords: [browser, web, url, search, screenshot, webpage]
  tools: [browser]
---

# Web Browsing and Automation

Use browser capabilities to read web pages, PDFs, search, and take screenshots.

## Available Functions

```python
# Read a webpage in text format
read_url(url: str) -> str

# Search for information
search(query: str, engine: Literal["google", "duckduckgo", "perplexity"]) -> str

# Take a screenshot of a webpage
screenshot_url(url: str, path: Optional[Path]) -> Path

# Read browser console logs
read_logs() -> str
```

## Best Practices

1. **Start with search for unknown URLs**: Use `search()` to find relevant pages
2. **Read before extracting**: Use `read_url()` to see page content
3. **Check logs for errors**: Use `read_logs()` after reading complex pages
4. **Screenshot for visual verification**: Useful for checking layouts or UI

## Common Patterns

### Searching and Reading
```python
# Search for information
search("gptme documentation getting started")

# Read a specific page
read_url("https://gptme.org/docs/getting-started.html")
```

### Using Perplexity for Research
```python
# Get AI-powered answers with sources
search("latest developments in LLM agents", "perplexity")
```

**Note**: Perplexity search requires either:
- `PERPLEXITY_API_KEY` - Direct access to Perplexity API
- `OPENROUTER_API_KEY` - Uses Perplexity via OpenRouter (model: `perplexity/sonar-pro`)

If both keys are available, PERPLEXITY_API_KEY takes precedence.

### Taking Screenshots
```python
# Screenshot and view
screenshot_url("https://gptme.org")
```

### Reading PDFs
```python
# Read arXiv paper (default: first 10 pages)
read_url("https://arxiv.org/pdf/2410.12361v2")

# Read more pages
read_url("https://arxiv.org/pdf/2410.12361v2", max_pages=20)

# Read all pages
read_url("https://example.com/document.pdf", max_pages=0)
```

**Note**: PDF support uses pypdf library for text extraction. PDFs are automatically detected by:
- URL ending in `.pdf`
- Content-Type header containing `application/pdf`

**Vision Alternative**: For scanned/complex PDFs with garbled text, convert to images and use vision.

### Debugging Web Pages
```python
# Read page
read_url("https://example.com")

# Check for JavaScript errors
read_logs()
```

## When to Use Browser vs Direct Tools

- Use **browser** for: Web research, documentation, public information
- Use **shell** for: Local files, git operations, system commands
- Use **ipython** for: Data processing, calculations, API calls
