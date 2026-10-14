# pollen.platform

The platform package holds the OS-specific shims (Linux, macOS, Windows) behind one PlatformShim, so
the rest of pollen never branches on the host operating system: process groups, the facts a device
reports about itself, where its data lives, how the packet is installed as a service and, for
colonization, which interpreters to try and where a virtual environment keeps its programs.
