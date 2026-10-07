using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

// Self-contained WeChat 4.x (Windows) database key recovery + SQLCipher page decryption.
// Only P/Invoke into kernel32 and the .NET Framework crypto classes are used.
public static class WxKey
{
    // ---------- Win32 ----------
    [StructLayout(LayoutKind.Sequential)]
    struct MEMORY_BASIC_INFORMATION
    {
        public IntPtr BaseAddress;
        public IntPtr AllocationBase;
        public uint AllocationProtect;
        public IntPtr RegionSize;
        public uint State;
        public uint Protect;
        public uint Type;
    }

    const uint PROCESS_VM_READ = 0x0010;
    const uint PROCESS_QUERY_INFORMATION = 0x0400;
    const uint MEM_COMMIT = 0x1000;
    const uint MEM_PRIVATE = 0x20000;

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, IntPtr size, out IntPtr read);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern int VirtualQueryEx(IntPtr h, IntPtr addr, out MEMORY_BASIC_INFORMATION mbi, IntPtr len);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CloseHandle(IntPtr h);

    // 26 bytes that follow six wildcard bytes in the in-memory key stub
    static readonly byte[] MemTail = new byte[]
    {
        0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
        0x20,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
        0x2F,0x00,0x00,0x00,0x00,0x00,0x00,0x00
    };

    // ---------- helpers ----------
    static string Hex(byte[] b) { StringBuilder sb = new StringBuilder(b.Length * 2); foreach (byte x in b) sb.Append(x.ToString("x2")); return sb.ToString(); }
    static string Hex(byte[] b, int len)
    {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < len && i < b.Length; i++) sb.Append(b[i].ToString("x2")).Append(' ');
        return sb.ToString().Trim();
    }
    static byte[] UnHex(string s)
    {
        byte[] r = new byte[s.Length / 2];
        for (int i = 0; i < r.Length; i++) r[i] = Convert.ToByte(s.Substring(i * 2, 2), 16);
        return r;
    }

    static byte[] Pbkdf2Sha512(byte[] password, byte[] salt, int iterations, int dkLen)
    {
        using (HMACSHA512 hmac = new HMACSHA512(password))
        {
            int hLen = 64;
            int blocks = (int)Math.Ceiling((double)dkLen / hLen);
            byte[] output = new byte[blocks * hLen];
            for (int i = 1; i <= blocks; i++)
            {
                byte[] input = new byte[salt.Length + 4];
                Buffer.BlockCopy(salt, 0, input, 0, salt.Length);
                input[salt.Length] = (byte)(i >> 24);
                input[salt.Length + 1] = (byte)(i >> 16);
                input[salt.Length + 2] = (byte)(i >> 8);
                input[salt.Length + 3] = (byte)i;
                byte[] u = hmac.ComputeHash(input);
                byte[] t = (byte[])u.Clone();
                for (int j = 1; j < iterations; j++)
                {
                    u = hmac.ComputeHash(u);
                    for (int k = 0; k < hLen; k++) t[k] ^= u[k];
                }
                Buffer.BlockCopy(t, 0, output, (i - 1) * hLen, hLen);
            }
            if (output.Length == dkLen) return output;
            byte[] cut = new byte[dkLen];
            Buffer.BlockCopy(output, 0, cut, 0, dkLen);
            return cut;
        }
    }

    static bool IsPotentialKey(byte[] key)
    {
        if (key.Length != 32) return false;
        HashSet<byte> distinct = new HashSet<byte>(key);
        if (distinct.Count < 15) return false;
        int printable = 0;
        foreach (byte b in key) if (b >= 32 && b <= 126) printable++;
        return printable <= 24;
    }

    // ---------- step 1: internal key inside Weixin.dll ----------
    // pattern: 48 BA <8> [3..8 bytes] 48 BA <8> [3..8] 48 BA <8> [3..8] 48 BA <8> [3..8] 48 85 C0
    public static string ScanDllForInternalKeys(string dllPath)
    {
        StringBuilder log = new StringBuilder();
        FileStream fs = File.OpenRead(dllPath);
        byte[] head = new byte[0x1000];
        fs.Read(head, 0, head.Length);
        int e_lfanew = BitConverter.ToInt32(head, 0x3C);
        fs.Position = e_lfanew;
        byte[] peHdr = new byte[0x400];
        fs.Read(peHdr, 0, peHdr.Length);
        ushort machine = BitConverter.ToUInt16(peHdr, 4);
        ushort numSections = BitConverter.ToUInt16(peHdr, 6);
        ushort optSize = BitConverter.ToUInt16(peHdr, 20);
        int optOff = 24;
        uint imageBase = machine == 0x8664 ? BitConverter.ToUInt32(peHdr, optOff + 24) : 0;
        int sectOff = optOff + optSize;
        HashSet<string> keys = new HashSet<string>();
        int scanned = 0;
        for (int s = 0; s < numSections; s++)
        {
            int o = sectOff + s * 40;
            uint characteristics = BitConverter.ToUInt32(peHdr, o + 36);
            if ((characteristics & 0x20000000) == 0) continue;      // IMAGE_SCN_CNT_CODE
            uint rawSize = BitConverter.ToUInt32(peHdr, o + 16);
            uint rawPtr = BitConverter.ToUInt32(peHdr, o + 20);
            if (rawSize == 0 || rawSize > 400u * 1024 * 1024) continue;
            byte[] data = new byte[rawSize];
            fs.Position = rawPtr;
            int got = 0, r;
            while (got < data.Length && (r = fs.Read(data, got, data.Length - got)) > 0) got += r;
            scanned += got;
            for (int i = 0; i + 90 < got; i++)
            {
                if (data[i] != 0x48 || data[i + 1] != 0xBA) continue;
                byte[] k0 = new byte[8], k1 = new byte[8], k2 = new byte[8], k3 = new byte[8];
                Buffer.BlockCopy(data, i + 2, k0, 0, 8);
                for (int g1 = 3; g1 <= 8; g1++)
                {
                    int p1 = i + 10 + g1;
                    if (p1 + 10 > got || data[p1] != 0x48 || data[p1 + 1] != 0xBA) continue;
                    Buffer.BlockCopy(data, p1 + 2, k1, 0, 8);
                    for (int g2 = 3; g2 <= 8; g2++)
                    {
                        int p2 = p1 + 10 + g2;
                        if (p2 + 10 > got || data[p2] != 0x48 || data[p2 + 1] != 0xBA) continue;
                        Buffer.BlockCopy(data, p2 + 2, k2, 0, 8);
                        for (int g3 = 3; g3 <= 8; g3++)
                        {
                            int p3 = p2 + 10 + g3;
                            if (p3 + 10 > got || data[p3] != 0x48 || data[p3 + 1] != 0xBA) continue;
                            Buffer.BlockCopy(data, p3 + 2, k3, 0, 8);
                            for (int g4 = 3; g4 <= 8; g4++)
                            {
                                int p4 = p3 + 10 + g4;
                                if (p4 + 3 > got) continue;
                                if (data[p4] == 0x48 && data[p4 + 1] == 0x85 && data[p4 + 2] == 0xC0)
                                {
                                    byte[] full = new byte[32];
                                    Buffer.BlockCopy(k0, 0, full, 0, 8);
                                    Buffer.BlockCopy(k1, 0, full, 8, 8);
                                    Buffer.BlockCopy(k2, 0, full, 16, 8);
                                    Buffer.BlockCopy(k3, 0, full, 24, 8);
                                    keys.Add(Hex(full));
                                }
                            }
                        }
                    }
                }
            }
        }
        fs.Close();
        log.AppendLine("codeBytesScanned=" + scanned + " candidates=" + keys.Count);
        foreach (string k in keys) log.AppendLine("KEY " + k);
        return log.ToString();
    }

    // ---------- step 2: candidate raw keys from process memory ----------
    public static string ScanProcessMemory(int pid)
    {
        StringBuilder log = new StringBuilder();
        IntPtr h = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, false, pid);
        if (h == IntPtr.Zero) { log.AppendLine("OpenProcess failed err=" + Marshal.GetLastWin32Error()); return log.ToString(); }
        try
        {
            List<IntPtr> ptrs = new List<IntPtr>();
            long addr = 0;
            long total = 0, regions = 0;
            MEMORY_BASIC_INFORMATION mbi;
            int mbiSize = Marshal.SizeOf(typeof(MEMORY_BASIC_INFORMATION));
            while (VirtualQueryEx(h, new IntPtr(addr), out mbi, new IntPtr(mbiSize)) != 0)
            {
                long baseAddr = mbi.BaseAddress.ToInt64();
                long size = mbi.RegionSize.ToInt64();
                if (size <= 0) break;
                if (mbi.State == MEM_COMMIT && mbi.Type == MEM_PRIVATE && size <= 512L * 1024 * 1024)
                {
                    regions++; total += size;
                    int cap = (int)size;
                    byte[] buf = new byte[cap];
                    IntPtr read;
                    if (ReadProcessMemory(h, mbi.BaseAddress, buf, new IntPtr(cap), out read) && read.ToInt64() > 0)
                    {
                        int got = (int)read.ToInt64();
                        for (int i = 0; i + 32 <= got; i++)
                        {
                            if (buf[i + 6] != MemTail[0]) continue;
                            bool ok = true;
                            for (int k = 0; k < 26; k++) if (buf[i + 6 + k] != MemTail[k]) { ok = false; break; }
                            if (ok) ptrs.Add(new IntPtr(BitConverter.ToInt64(buf, i)));
                        }
                    }
                }
                addr = baseAddr + size;
            }
            log.AppendLine("regions=" + regions + " bytes=" + total + " pointerHits=" + ptrs.Count);
            HashSet<string> seen = new HashSet<string>();
            List<string> outKeys = new List<string>();
            byte[] keyBuf = new byte[32];
            foreach (IntPtr p in ptrs)
            {
                IntPtr read;
                if (!ReadProcessMemory(h, p, keyBuf, new IntPtr(32), out read)) continue;
                if (read.ToInt64() != 32) continue;
                if (!IsPotentialKey(keyBuf)) continue;
                string hex = Hex(keyBuf);
                if (seen.Add(hex)) outKeys.Add(hex);
            }
            log.AppendLine("candidates=" + outKeys.Count);
            foreach (string k in outKeys) log.AppendLine("CAND " + k);
        }
        finally { CloseHandle(h); }
        return log.ToString();
    }

    // ---------- step 3: verify candidates against a real DB page ----------
    public static string VerifyKeys(string dbPath, string candidatesHex, string internalKeysHex)
    {
        StringBuilder log = new StringBuilder();
        byte[] buf = new byte[4096];
        using (FileStream fs = new FileStream(dbPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        {
            int got = fs.Read(buf, 0, 4096);
            if (got != 4096) { log.AppendLine("db too small"); return log.ToString(); }
        }
        List<string> cands = new List<string>();
        foreach (string line in candidatesHex.Split('\n')) { string s = line.Trim(); if (s.Length == 64) cands.Add(s); }
        List<string> internals = new List<string>();
        if (string.IsNullOrEmpty(internalKeysHex)) internals.Add(new string('0', 64));
        else foreach (string line in internalKeysHex.Split('\n')) { string s = line.Trim(); if (s.Length == 64) internals.Add(s); }
        log.AppendLine("testing " + cands.Count + " candidates x " + internals.Count + " internal keys");
        byte[] salt = new byte[16];
        Buffer.BlockCopy(buf, 0, salt, 0, 16);
        byte[] mac_salt = new byte[16];
        for (int i = 0; i < 16; i++) mac_salt[i] = (byte)(salt[i] ^ 0x3A);
        foreach (string cand in cands)
        {
            foreach (string intern in internals)
            {
                byte[] c = UnHex(cand), ik = UnHex(intern);
                byte[] pass = new byte[32];
                for (int i = 0; i < 32; i++) pass[i] = (byte)(c[i] ^ ik[i]);
                byte[] newKey = Pbkdf2Sha512(pass, salt, 256000, 32);
                byte[] macKey = Pbkdf2Sha512(newKey, mac_salt, 2, 32);
                using (HMACSHA512 hmac = new HMACSHA512(macKey))
                {
                    byte[] data = new byte[4032 - 16 + 4];
                    Buffer.BlockCopy(buf, 16, data, 0, 4032 - 16);
                    data[data.Length - 4] = 1; data[data.Length - 3] = 0; data[data.Length - 2] = 0; data[data.Length - 1] = 0;
                    byte[] mac = hmac.ComputeHash(data);
                    bool match = true;
                    for (int i = 0; i < 64; i++) if (mac[i] != buf[4032 + i]) { match = false; break; }
                    if (match)
                    {
                        log.AppendLine("MATCH");
                        log.AppendLine("FINAL_KEY " + Hex(pass));
                        log.AppendLine("RAW_KEY " + cand);
                        log.AppendLine("INTERNAL_KEY " + intern);
                        return log.ToString();
                    }
                }
            }
        }
        log.AppendLine("no match");
        return log.ToString();
    }

    // ---------- step 4: decrypt a SQLCipher database ----------
    public static string ProbePage1(string dbPath, string passphraseHex)
    {
        StringBuilder log = new StringBuilder();
        byte[] buf = new byte[4096];
        using (FileStream fs = new FileStream(dbPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            fs.Read(buf, 0, 4096);
        byte[] pass = UnHex(passphraseHex);
        byte[] salt = new byte[16];
        Buffer.BlockCopy(buf, 0, salt, 0, 16);
        byte[] newKey = Pbkdf2Sha512(pass, salt, 256000, 32);
        log.AppendLine("salt=" + Hex(salt));
        log.AppendLine("newKey=" + Hex(newKey));
        string[] keyNames = new string[] { "pass", "newKey" };
        byte[][] keys = new byte[][] { pass, newKey };
        // (ciphertext offset, ciphertext length, iv offset, iv from start of page)
        int[][] layouts = new int[][]
        {
            new int[] { 16, 4000, 4016 },
            new int[] { 0, 4016, 4016 },
            new int[] { 16, 4000, 0 },
            new int[] { 32, 3984, 16 },
            new int[] { 16, 4064, 0 },
        };
        for (int ki = 0; ki < 2; ki++)
        {
            for (int li = 0; li < layouts.Length; li++)
            {
                try
                {
                    int ctOff = layouts[li][0], ctLen = layouts[li][1], ivOff = layouts[li][2];
                    if (ctOff + ctLen > 4096) continue;
                    using (Aes aes = Aes.Create())
                    {
                        aes.Mode = CipherMode.CBC;
                        aes.Padding = PaddingMode.None;
                        aes.Key = keys[ki];
                        byte[] iv = new byte[16];
                        Buffer.BlockCopy(buf, ivOff, iv, 0, 16);
                        aes.IV = iv;
                        using (ICryptoTransform dec = aes.CreateDecryptor())
                        {
                            byte[] plain = dec.TransformFinalBlock(buf, ctOff, ctLen);
                            string head = Encoding.ASCII.GetString(plain, 0, Math.Min(24, plain.Length)).Replace("\0", "\\0");
                            log.AppendLine("key=" + keyNames[ki] + " ct[" + ctOff + "+" + ctLen + "] iv@" + ivOff + " -> " + head + (head.StartsWith("SQLite") ? "   <<< MATCH" : ""));
                        }
                    }
                }
                catch (Exception ex) { log.AppendLine("layout " + li + " key " + keyNames[ki] + " EX " + ex.Message); }
            }
        }
        return log.ToString();
    }

    public static string BruteLayout(string dbPath, string passphraseHex)
    {
        StringBuilder log = new StringBuilder();
        byte[] buf = new byte[4096];
        using (FileStream fs = new FileStream(dbPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            fs.Read(buf, 0, 4096);
        byte[] pass = UnHex(passphraseHex);
        byte[] salt = new byte[16];
        Buffer.BlockCopy(buf, 0, salt, 0, 16);
        byte[] newKey = Pbkdf2Sha512(pass, salt, 256000, 32);
        string[] kNames = { "pass", "newKey" };
        byte[][] keys = { pass, newKey };
        int[] ctOffs = { 0, 16, 32 };
        int[] ctLens = { 3984, 4000, 4016, 4032, 4064 };
        int[] ivOffs = { 0, 16, 32, 4000, 4016 };
        for (int ki = 0; ki < keys.Length; ki++)
        {
            for (int ci = 0; ci < ctOffs.Length; ci++)
            {
                for (int li = 0; li < ctLens.Length; li++)
                {
                    int ctOff = ctOffs[ci], ctLen = ctLens[li];
                    if (ctLen % 16 != 0 || ctOff + ctLen > 4096) continue;
                    for (int vi = 0; vi < ivOffs.Length; vi++)
                    {
                        byte[] plain;
                        try
                        {
                            using (Aes aes = Aes.Create())
                            {
                                aes.Mode = CipherMode.CBC;
                                aes.Padding = PaddingMode.None;
                                aes.Key = keys[ki];
                                byte[] iv = new byte[16];
                                Buffer.BlockCopy(buf, ivOffs[vi], iv, 0, 16);
                                aes.IV = iv;
                                using (ICryptoTransform dec = aes.CreateDecryptor())
                                    plain = dec.TransformFinalBlock(buf, ctOff, ctLen);
                            }
                        }
                        catch { continue; }
                        string ascii = Encoding.ASCII.GetString(plain, 0, Math.Min(16, plain.Length));
                        bool magic = ascii.Contains("SQLite");
                        // structured sqlite page 1 tail: page size 0x1000 big endian at some offset
                        for (int m = 0; m + 1 < Math.Min(40, plain.Length); m++)
                            if (plain[m] == 0x10 && plain[m + 1] == 0x00) { magic = true; break; }
                        if (magic)
                            log.AppendLine("HIT key=" + kNames[ki] + " ct[" + ctOff + "+" + ctLen + "] iv@" + ivOffs[vi] + " bytes=" + Hex(plain, 24));
                    }
                }
            }
        }
        if (log.Length == 0) log.AppendLine("no hit");
        return log.ToString();
    }

    // The value printed as FINAL_KEY by VerifyKeys is the SQLCipher *passphrase*.
    // The AES key is PBKDF2(passphrase, salt, 256000, SHA512, 32) where salt is the
    // first 16 bytes of the file being decrypted, so it must be derived per database.
    public static string DecryptDb(string srcPath, string dstPath, string passphraseHex)
    {
        StringBuilder log = new StringBuilder();
        byte[] pass = UnHex(passphraseHex);
        const int PAGE = 4096, RESERVE = 80, IV = 16;
        byte[] all;
        using (FileStream fs = new FileStream(srcPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        {
            byte[] raw = new byte[fs.Length];
            int got = 0, r;
            while (got < raw.Length && (r = fs.Read(raw, got, raw.Length - got)) > 0) got += r;
            if (got == raw.Length) all = raw;
            else { all = new byte[got]; Buffer.BlockCopy(raw, 0, all, 0, got); }
        }
        byte[] salt = new byte[16];
        Buffer.BlockCopy(all, 0, salt, 0, 16);
        byte[] key = Pbkdf2Sha512(pass, salt, 256000, 32);
        log.AppendLine("salt=" + Hex(salt));
        int pages = all.Length / PAGE;
        log.AppendLine("pages=" + pages + " bytes=" + all.Length);
        byte[] outBuf = new byte[pages * PAGE];
        using (Aes aes = Aes.Create())
        {
            aes.Mode = CipherMode.CBC;
            aes.Padding = PaddingMode.None;
            aes.Key = key;
            for (int p = 0; p < pages; p++)
            {
                int off = p * PAGE;
                int dataStart = (p == 0) ? 16 : 0;
                int dataLen = PAGE - RESERVE - dataStart;
                byte[] iv = new byte[IV];
                Buffer.BlockCopy(all, off + PAGE - RESERVE, iv, 0, IV);
                aes.IV = iv;
                using (ICryptoTransform dec = aes.CreateDecryptor())
                {
                    byte[] plain = dec.TransformFinalBlock(all, off + dataStart, dataLen);
                    if (p == 0)
                    {
                        log.AppendLine("page1 plaintext head = " + Encoding.ASCII.GetString(plain, 0, 16).Replace("\0", "\\0"));
                        // The 16-byte salt on disk replaces SQLite's magic string, which is
                        // not stored; restore it so the output is a valid SQLite database.
                        byte[] magicBytes = Encoding.ASCII.GetBytes("SQLite format 3\0");
                        Buffer.BlockCopy(magicBytes, 0, outBuf, 0, 16);
                        Buffer.BlockCopy(plain, 0, outBuf, 16, plain.Length);
                    }
                    else
                    {
                        Buffer.BlockCopy(plain, 0, outBuf, off, plain.Length);
                    }
                }
            }
        }
        Directory.CreateDirectory(Path.GetDirectoryName(dstPath));
        File.WriteAllBytes(dstPath, outBuf);
        string magic = Encoding.ASCII.GetString(outBuf, 0, 16);
        log.AppendLine("header=" + magic.Replace("\0", "\\0"));
        log.AppendLine("ok=" + magic.StartsWith("SQLite format 3"));
        return log.ToString();
    }
}
