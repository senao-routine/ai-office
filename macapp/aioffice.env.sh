# AI Office P4 常駐のパス単一定義（install.sh / uninstall.sh / officectl.sh が source する）
# ~/Library/Application Support は非TCC保護＝launchd が FDA 無しで実行できる置き場（掟）。
AIOFFICE_DEST_DEFAULT="$HOME/Library/Application Support/AIOffice"
AIOFFICE_LABEL="com.senao.aioffice"
AIOFFICE_PLIST="$HOME/Library/LaunchAgents/com.senao.aioffice.plist"
# P4.5: relay_agent 常駐（スマホ配達を常時オン）の2本目 LaunchAgent
AIOFFICE_RELAY_LABEL="com.senao.aioffice.relay"
AIOFFICE_RELAY_PLIST="$HOME/Library/LaunchAgents/com.senao.aioffice.relay.plist"
