//! Windows helper using rustunes' original MIT-licensed fixed-profile signer.
//! Pairing keys never enter this process. Only an ATC reply and the exact
//! operation bytes are read from stdin; only the 21-byte sidecar is returned.
use std::io::{self, Read, Write};
use anyhow::{Context, Result, bail, ensure};

#[path = "../../rustunes/crates/music-protocol/src/protocol/signing.rs"]
mod signing;

fn run() -> Result<()> {
    let command = std::env::args().nth(1).context("expected request or sign")?;
    if command == "request" {
        io::stdout().write_all(&signing::HOST_REQUEST)?;
        return Ok(());
    }
    if command == "--version" {
        println!("ipods-music-signer 0.1.0 (rustunes 057ee8fc9c9d)");
        return Ok(());
    }
    if command != "sign" { bail!("expected request or sign"); }
    let mut bytes = Vec::new();
    io::stdin().take((signing::MAX_OPERATION_BYTES + 88) as u64).read_to_end(&mut bytes)?;
    ensure!(bytes.len() >= 88, "incomplete signing input");
    let size = u32::from_le_bytes(bytes[..4].try_into()?) as usize;
    ensure!(size > 0 && size <= signing::MAX_OPERATION_BYTES, "operation exceeds bounds");
    ensure!(bytes.len() == 4 + 83 + size, "signing input length differs");
    let session = signing::FixedHostSession::from_reply(&signing::HOST_REQUEST, &bytes[4..87])?;
    let signature = session.sign(&bytes[87..])?;
    io::stdout().write_all(&signature)?;
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        // Error descriptions contain no reply, operation or key bytes.
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
