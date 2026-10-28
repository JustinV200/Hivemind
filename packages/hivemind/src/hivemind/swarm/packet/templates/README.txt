POLLEN PACKET for "@@LABEL@@" (@@PLATFORM@@)

This folder joins one machine to the Hive. Run one script and it will:
  1. install Tailscale if it is missing, and join the Hive's private network;
  2. check that the Hive Stand (@@HIVE_HOST@@, port @@HIVE_PORT@@) answers;
  3. install pollen for this account and enrol the machine;
  4. set pollen to start at every login. It reconnects by itself on any Wi-Fi.

HOW TO USE IT
  Copy this whole folder to the machine, open a terminal in it, and run:
      @@RUN@@
  Run it as the account pollen should belong to. You may be asked to allow
  an install or for your password.

IMPORTANT
  - Use it before @@EXPIRES@@. After that it no longer works.
  - It works once. The secrets/ folder is deleted when the script ends, even
    if something failed. If it failed, ask for a new packet.
  - Until then, secrets/ is a key to the Hive: carry it yourself, never by
    chat, email or a shared drive.
  - Running it again on the same machine is safe; finished steps are skipped.
