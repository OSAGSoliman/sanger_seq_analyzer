# Deploying the Sanger Seq Analyzer lab server

The server edition runs the same analysis as the desktop app, but in the browser, for the
whole lab: QC runs, reviews (who confirmed PASS/FAIL, and when), Fetch data and the MeaNS
template are kept in one place. People open an address such as
`https://seqqc.lab.local:8443` in Edge or Chrome; nothing is installed on their PCs.

The desktop app keeps working as before. Both use the same analysis, and they never save
to the same MeaNS template at the same moment (a lock file next to the template).

## Checklist for IT

| Item | What is needed |
|---|---|
| Server | Windows Server 2016 or newer **or** a Linux VM with Docker. 2 CPU cores, 4 GB RAM, 20 GB disk for the data (about 60 MB per 96-sample run). |
| Network | Inside the hospital network only. One TCP port (8443 by default) open to the lab's PCs. No internet access is needed at runtime. |
| Certificate | A certificate for the server's name from the hospital's certificate authority, as PEM files: `server.crt` (with the chain) and `server.key`. Without it the server runs plain HTTP, which is only acceptable for a first test. |
| Account | The account the service runs as needs **Modify** rights on the shared folders it uses (the sequencer export folder: read; the MeaNS template folder: modify, because saving writes a backup and a temporary file, then renames). |
| Backups | A nightly `server backup` (see below) copied to the hospital's backup system. Do not back up the live database files (`sanger_seq.db`, `-wal`) directly. |
| Data | The data folder holds patient data from the lab spreadsheets: names, MRN and EID in uploaded files. Restrict it to administrators and the service account (the Windows installer does this). |

## Windows Server

1. Download `SangerSeqServer-windows.zip` from the release and unzip it on the server.
2. Open PowerShell **as administrator** in that folder and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1 -CertFile C:\certs\server.crt -KeyFile C:\certs\server.key
   ```

   This installs to `C:\Program Files\SangerSeqServer`, keeps the data in
   `C:\ProgramData\SangerSeqServer` (readable only by administrators and the service),
   opens port 8443 in the firewall (domain and private networks), and starts the
   **Sanger Seq Analyzer server** service. It restarts automatically after a failure and
   at boot. Options: `-Port 9443`, `-DataDir D:\SangerSeqData`, `-NoFirewall`.
3. Create the first admin (it asks for a password of at least 12 characters):

   ```powershell
   & "C:\Program Files\SangerSeqServer\SangerSeqServer\SangerSeqServer.exe" server --data C:\ProgramData\SangerSeqServer create-admin
   ```

4. Open `https://<server name>:8443/`, sign in, and under **Admin**:
   - **Users**: add each person with a role. They receive a temporary password and choose their own at first sign-in.
   - **Folders**: add the shared folders as UNC paths, for example `\\fileserver\Virology\Sanger` and `\\fileserver\Virology\Measles` (tick *server may write here* for the folder of the MeaNS template). Then press **Check access**.
   - **Lab defaults**: the rules new runs start with.
5. Under **Fetch data › Source files**, choose for each file (masterlist, daily sheet,
   line list, MeaNS template) the shared folder it is in. The newest file whose name
   matches the lab's naming is used automatically.

**Service account and shares.** The service runs as `NT AUTHORITY\NetworkService`. On
the network it then acts as the server's computer account (`DOMAIN\SERVERNAME$`), so the
simplest set-up is to give that computer account access to the shares. To use a domain
service account or a gMSA instead, open `services.msc` → *Sanger Seq Analyzer server* →
*Log On*, enter the account, and give it Modify rights on `C:\ProgramData\SangerSeqServer`.
Use UNC paths (`\\server\share`), never mapped drive letters: a service cannot see them.

**Upgrade.** Unzip the new release and run `upgrade.ps1` as administrator from it. It stops
the service, makes a backup, replaces the program, and starts the service; the database
is upgraded at start (after its own backup in `backups\`).

**Remove.** `uninstall.ps1` (as administrator) removes the service and program and keeps
the data folder; add `-RemoveData` to delete it too.

The service is WinSW 2.12 (MIT licence, see `WinSW-LICENSE.txt`); its log files are in
`C:\ProgramData\SangerSeqServer\logs` next to the server's own `server.log`.

## Linux with Docker

```bash
# on the Docker host: mount the lab shares (read-only for the sequencer, read-write for the template folder)
sudo mount -t cifs //fileserver/Virology /mnt/virology -o credentials=/root/.smb-sangerseq,uid=10001,gid=10001,vers=3.0

mkdir -p sanger-seq/data && cd sanger-seq
sudo chown 10001:10001 data                    # the container runs as user 10001
cp /path/to/repo/packaging/server/docker-compose.yml .
# load the image: from the release, or for testing from the "sanger-seq-server-image" artifact of a CI run
# (that artifact also has a docker-compose.yml ready for the loaded image)
gunzip -c sanger-seq-server-image.tar.gz | docker load
# edit docker-compose.yml: image tag, and the "/mnt/virology:/shares/virology" volume line
docker compose up -d
docker compose exec sanger-seq sanger-seq server create-admin
```

HTTPS: put `server.crt` and `server.key` in `data/certs/` (owned by 10001, key mode 600),
and set in `data/config.toml`:

```toml
tls_cert = "certs/server.crt"
tls_key = "certs/server.key"
```

then `docker compose restart`. In **Admin › Folders** add the shares by their path inside
the container (e.g. `/shares/virology/Sanger`). The image is also published as
`ghcr.io/osagsoliman/sanger-seq-server:<version>`.

Upgrade: load the new image, change the tag in `docker-compose.yml`, `docker compose up -d`.

## Settings (`config.toml` in the data folder)

| Setting | Default | Meaning |
|---|---|---|
| `host`, `port` | `0.0.0.0`, `8443` | address and port to listen on |
| `tls_cert`, `tls_key` | empty | PEM files for HTTPS (relative to the data folder or absolute) |
| `session_idle_minutes` | 30 | sign out after this much inactivity |
| `session_max_hours` | 12 | longest sign-in |
| `timezone` | `Asia/Dubai` | local time for TAT, ISO weeks of strain names and times shown |
| `max_ab1_mb`, `max_files_per_run`, `max_sheet_mb` | 10, 500, 100 | upload limits |
| `public_url` | empty | the address users type; when set, changes must come from it |

Restart the service after editing.

## Commands

All take `--data <data folder>` (or the `SANGER_SEQ_DATA` environment variable). On Windows
run `SangerSeqServer.exe …`; in Docker `docker compose exec sanger-seq sanger-seq …`.

| Command | What it does |
|---|---|
| `serve` | run the server (the service does this) |
| `server create-admin` | create an admin account |
| `server add-user --username X --role analyst` | add a user; prints a temporary password |
| `server passwd --username X` | new temporary password (also unlocks after too many wrong passwords) |
| `server backup [--out file.zip]` | consistent backup of the database, runs, uploads and template versions (safe while running) |
| `server check` | settings, database, users, audit-log integrity and folder access |
| `server health` | exit 0 when the running server answers |

**Restore** from a backup zip: stop the service, unzip it into an empty data folder (keep
your `certs/`), start the service.

After installing, `smoke.py` (in the Windows zip, or `packaging/server/smoke.py`) checks a
running server end to end with the bundled examples:
`python smoke.py --base https://<server>:8443 --user <admin> --examples examples` (password in `SSA_PW`).

## Roles

| Role | Can |
|---|---|
| Viewer | look at runs and download results |
| Analyst | also start runs, review samples (Confirm PASS/FAIL), use Fetch data, save to the MeaNS template |
| Admin | also manage users, folders and lab defaults, delete runs, read the audit log |

## Security notes

- Passwords are stored as scrypt hashes; 10 wrong passwords lock an account for 15 minutes.
- Sign-ins end after 30 minutes without activity, and when the password or role changes.
- Every change is checked against a per-sign-in token and must come from the server's own pages.
- The pages load nothing from the internet. The sample viewer runs in a sandboxed frame.
- The audit log (Admin › Audit log) records sign-ins, runs, reviews, exports, uploads,
  template saves and admin changes. Each entry is chained to the one before, so an edited
  or deleted entry shows. It never contains patient names; it does contain accessions and
  strain names.
- Fetch data keeps each user's linked patient rows in memory only, dropped after 2 hours
  without use. Uploaded lab files are kept in `data/sources/` (they contain patient data;
  delete old ones under that folder when they are no longer needed).
