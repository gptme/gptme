# Fish completion for gptme - no file fallback for options

# Cache variables to avoid repeated expensive calls
set -g __gptme_models_cache ""
set -g __gptme_tools_cache ""
set -g __gptme_conversations_cache ""
set -g __gptme_cache_time 0

# Helper function to check if cache is fresh (5 minutes)
function __gptme_cache_fresh
    set -l current_time (date +%s)
    test (math $current_time - $__gptme_cache_time) -lt 300
end

# Dynamic model completion using gptme-util
function __gptme_models
    if not __gptme_cache_fresh; or test -z "$__gptme_models_cache"
        set -l models_list (gptme-util models list --simple 2>/dev/null)

        # Fallback to common models if command fails
        if test -z "$models_list"
            set models_list "openai/gpt-4o openai/gpt-4o-mini openai/gpt-4-turbo anthropic/claude-3-5-sonnet-20241022 anthropic/claude-3-5-haiku-20241022 anthropic/claude-3-opus-20240229"
        end

        set -g __gptme_models_cache (string join " " $models_list)
        set -g __gptme_cache_time (date +%s)
    end
    string split " " $__gptme_models_cache
end

# Fast tools completion - use common tools
function __gptme_tools
    if test -z "$__gptme_tools_cache"
        set -g __gptme_tools_cache "shell ipython browser patch save read append screenshot vision tmux gh computer rag chats"
    end
    string split " " $__gptme_tools_cache
end

# Fast conversation completion - only load when actually needed
function __gptme_conversations
    if not __gptme_cache_fresh; or test -z "$__gptme_conversations_cache"
        set -g __gptme_conversations_cache (gptme-util chats ls -n 20 2>/dev/null | grep -E "^[0-9]" | awk '{print $1}' | string join " ")
        set -g __gptme_cache_time (date +%s)
    end
    string split " " $__gptme_conversations_cache
end

# Clear default completions
complete -c gptme -e

# Model completion - no file fallback
complete -c gptme -s m -l model -x -a "(__gptme_models)" -d "Model to use"

# Tools completion - no file fallback
complete -c gptme -s t -l tools -x -a "(__gptme_tools)" -d "Tools to allow"

# Name completion - no file fallback
complete -c gptme -l name -x -a "(__gptme_conversations)" -d "Conversation name"

# System completion - no file fallback
complete -c gptme -l system -x -a "full short" -d "System prompt"

# Tool format completion - no file fallback
complete -c gptme -l tool-format -x -a "markdown xml tool" -d "Tool format"

# Workspace directory completion (allow directories)
complete -c gptme -s w -l workspace -x -a "(__fish_complete_directories)" -d "Workspace directory"

# Boolean flags (no arguments)
complete -c gptme -s r -l resume -f -d "Load last conversation"
complete -c gptme -s y -l no-confirm -f -d "Skip confirmation prompts"
complete -c gptme -s n -l non-interactive -f -d "Non-interactive mode"
complete -c gptme -l no-stream -f -d "Don't stream responses"
complete -c gptme -l show-hidden -f -d "Show hidden system messages"
complete -c gptme -s v -l verbose -f -d "Verbose output"
complete -c gptme -l version -f -d "Show version"
complete -c gptme -s h -l help -f -d "Show help"

# File path completion for arguments (prompts can be file paths)
complete -c gptme -a "(__fish_complete_path)"
