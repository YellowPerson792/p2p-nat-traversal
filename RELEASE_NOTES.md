Prebuilt client and coordinator packages for P2P NAT traversal.

Client packages are available for Windows x64 and Ubuntu 24.04+ Linux x64. Extract the complete directory and run `p2p-overlay.exe` or `start-client.cmd` on Windows, or `./p2p-overlay` on Linux.

The Ubuntu 24.04+ Linux x64 server archive includes an installer. Extract it and run `sudo bash install.sh SERVER_IPV4`. Open TCP 8443 and UDP 3478. Review `SERVER.txt` before deployment.

The Python wheel is available for Python 3.12+ dependency installations. Every archive and wheel has a matching SHA256 file. Linux native packages require glibc 2.39 or newer. Windows binaries are not code-signed.

See `README.md` for full documentation.
