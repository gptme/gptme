.. rubric:: Example: Automated Code Review

This example demonstrates a simple and composable approach to automated code review using gptme and shell scripting.

1. Create a script called `review_pr.sh`:

   .. code-block:: bash

      #!/bin/bash
      # Usage: ./review_pr.sh <repo> <pr_number>

      repo=$1
      pr_number=$2

      # Fetch PR diff
      diff=$(gh pr view $pr_number --repo $repo --json diffUrl -q .diffUrl | xargs curl -s)

      # Generate review using gptme
      review=$(gptme --non-interactive "Review this pull request diff and provide constructive feedback:
      1. Identify potential bugs or issues.
      2. Suggest improvements for code quality and readability.
      3. Check for adherence to best practices.
      4. Highlight any security concerns.

      Pull Request Diff:
      $diff

      Format your review as a markdown list with clear, concise points.")

      # Post review comment
      gh pr comment $pr_number --repo $repo --body "## Automated Code Review

      $review

      *This review was generated automatically by gptme.*"

2. Make the script executable:

   .. code-block:: bash

      chmod +x review_pr.sh

3. Set up a GitHub Actions workflow (`.github/workflows/code_review.yml`):

   .. code-block:: yaml

      name: Automated Code Review
      on:
        pull_request:
          types: [opened, synchronize]

      jobs:
        review:
          runs-on: ubuntu-latest
          steps:
            - uses: actions/checkout@v2
            - name: Install gptme and GitHub CLI
              run: |
                pip install gptme
                gh auth login --with-token <<< "${{ secrets.GITHUB_TOKEN }}"
            - name: Run code review
              env:
                GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
              run: |
                ./review_pr.sh ${{ github.repository }} ${{ github.event.pull_request.number }}

This setup provides automated code reviews for your pull requests using gptme. It demonstrates how powerful automation can be achieved with minimal code and high composability.

Key points:

- Uses shell scripting for simplicity and ease of understanding
- Leverages gptme's non-interactive mode for automation
- Utilizes GitHub CLI (`gh`) for seamless GitHub integration
- Integrates with GitHub Actions for automated workflow

Benefits of this approach:

- Easily customizable: Adjust the gptme prompt to focus on specific aspects of code review
- Composable: The shell script can be extended or combined with other tools
- Minimal dependencies: Relies on widely available tools (bash, curl, gh)
- Quick setup: Can be implemented in any GitHub repository with minimal configuration

To customize this for your specific needs:

1. Modify the gptme prompt in `review_pr.sh` to focus on your project's coding standards
2. Add additional checks or integrations to the shell script as needed
3. Adjust the GitHub Actions workflow to fit your CI/CD pipeline

This example serves as a starting point for integrating gptme into your development workflow, demonstrating its potential for automating code review tasks.
