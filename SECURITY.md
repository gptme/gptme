# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in gptme, please report it responsibly.

### How to Report

1. **Do NOT open a public issue** for security vulnerabilities
2. Email the maintainers directly at: security@gptme.org (or contact via GitHub private disclosure)
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Any suggested fixes (optional)

### What to Expect

- Acknowledgment within 48 hours
- Regular updates on progress
- Credit for responsible disclosure (if desired)

### Scope

This policy applies to:
- The gptme CLI tool
- gptme-server
- gptme-webui
- Official gptme packages and plugins

## Security Model

gptme is designed to execute code on behalf of the user. Key security considerations:

- **Privilege Level**: gptme runs with user permissions - it can do anything you can do
- **Interactive Mode**: Commands require user confirmation before execution
- **Non-Interactive Mode**: Use only in trusted, isolated environments
- **Tool Execution**: All tool outputs are logged for audit purposes

See the [security documentation](https://gptme.org/docs/security.html) for detailed security guidance.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| < 1.0   | :x:                |

We recommend always using the latest version for security updates.
