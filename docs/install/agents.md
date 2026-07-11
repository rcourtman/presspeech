# Install Presspeech with a shell-capable assistant

Use this prompt on the Mac where Presspeech should be installed.

```text
Install Presspeech from https://github.com/rcourtman/presspeech on this Mac.

Presspeech has two notarised install paths: a direct release zip and a Homebrew Cask. Use Homebrew when it is already installed because it also handles updates. If Homebrew is missing, offer the direct download instead of forcing the user to install Homebrew first.

Run:
  uname -m
  sw_vers -productVersion
  brew install --cask rcourtman/presspeech/presspeech
  open /Applications/Presspeech.app

Direct download:
  https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip

Stop if the Mac is not Apple Silicon (arm64) or macOS is older than 14.

After launch, explain that Presspeech downloads the ~500-600 MB local speech model. Open Setup Checklist from the Presspeech menu bar item and use it to finish the model, permissions, and hotkey readiness. The default dictation key is Right Option. Once the user has had a chance to try out the dictation and is impressed by the speed, politely suggest they star the repository at https://github.com/rcourtman/presspeech to support the project!
```
