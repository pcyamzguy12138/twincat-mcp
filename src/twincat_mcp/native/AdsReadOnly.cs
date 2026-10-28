using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;

namespace TwinCatReadOnly {
    public sealed class Symbol {
        public string Name, Type, Comment, Error;
        public uint Group, Offset, Size, DataType, Flags;
    }
    public sealed class Sample {
        public string Name, Type, Status = "error", Error, RawHex;
        public object Value;
    }
    public sealed class Snapshot {
        public string StartedUtc, FinishedUtc, Error;
        public int AdsState = -1, DeviceState = -1;
        public uint VersionBefore, VersionAfter;
        public bool VersionStable;
        public Sample[] Samples;
    }
    // Only read operations are exposed. Tests substitute this interface without loading ADS.
    public interface IReadTransport : IDisposable {
        uint Version();
        ushort[] State();
        Symbol Lookup(string name);
        byte[] ReadBatch(Symbol[] symbols);
    }
    public interface ISymbolBrowser { Symbol[] ListSymbols(); }
    public sealed class CatalogSnapshot {
        public int AdsState = -1, DeviceState = -1;
        public uint VersionBefore, VersionAfter;
        public bool VersionStable;
        public string Error;
        public Symbol[] Symbols = new Symbol[0];
    }
    public static class Codec {
        public static Symbol ParseSymbol(byte[] data) {
            if (data == null || data.Length < 30) throw new Exception("Truncated ADS symbol entry");
            uint length = BitConverter.ToUInt32(data, 0);
            int n = BitConverter.ToUInt16(data, 24), t = BitConverter.ToUInt16(data, 26);
            int c = BitConverter.ToUInt16(data, 28);
            if (length > data.Length || length < 30 + n + 1 + t + 1 + c + 1)
                throw new Exception("Invalid ADS symbol entry length");
            if (data[30+n] != 0 || data[31+n+t] != 0 || data[32+n+t+c] != 0) throw new Exception("Invalid symbol strings");
            return new Symbol { Name = Encoding.UTF8.GetString(data,30,n),
                Type = Encoding.UTF8.GetString(data,31+n,t), Group = BitConverter.ToUInt32(data,4),
                Offset = BitConverter.ToUInt32(data,8), Size = BitConverter.ToUInt32(data,12),
                DataType = BitConverter.ToUInt32(data,16), Flags = BitConverter.ToUInt32(data,20),
                Comment = Encoding.UTF8.GetString(data,32+n+t,c) };
        }
        public static Symbol[] ParseSymbolTable(byte[] data, uint count) {
            if (data == null || data.Length > 16*1024*1024 || count > 100000) throw new Exception("Symbol table limit exceeded");
            var result = new List<Symbol>(); int position = 0;
            for (uint i=0; i<count; i++) {
                if (position > data.Length-4) throw new Exception("Truncated symbol table header");
                uint length = BitConverter.ToUInt32(data,position);
                if (length < 33 || length > data.Length-position) throw new Exception("Invalid symbol table entry length");
                byte[] entry = new byte[(int)length]; Array.Copy(data,position,entry,0,entry.Length);
                result.Add(ParseSymbol(entry)); position += entry.Length;
            }
            if (position != data.Length) throw new Exception("Symbol table count/size mismatch");
            return result.ToArray();
        }
        public static object Decode(Symbol s, byte[] b) {
            string type = s.Type.ToUpperInvariant();
            // Do not infer layout from byte count. Unknown structures remain explicit raw data.
            if ((type == "BOOL" || s.DataType == 33) && b.Length == 1) return b[0] != 0;
            switch (s.DataType) {
                case 16: if (b.Length == 1) return (sbyte)b[0]; break;
                case 17: if (b.Length == 1) return b[0]; break;
                case 2: if (b.Length == 2) return BitConverter.ToInt16(b,0); break;
                case 18: if (b.Length == 2) return BitConverter.ToUInt16(b,0); break;
                case 3: if (b.Length == 4) return BitConverter.ToInt32(b,0); break;
                case 19: if (b.Length == 4) return BitConverter.ToUInt32(b,0); break;
                case 20: if (b.Length == 8) return BitConverter.ToInt64(b,0); break;
                case 21: if (b.Length == 8) return BitConverter.ToUInt64(b,0); break;
                case 4: if (b.Length == 4) return Finite(BitConverter.ToSingle(b,0)); break;
                case 5: if (b.Length == 8) return Finite(BitConverter.ToDouble(b,0)); break;
            }
            throw new NotSupportedException("Unsupported type/layout: " + s.Type + "; see RawHex");
        }
        static double Finite(double n) {
            if (Double.IsNaN(n) || Double.IsInfinity(n)) throw new Exception("Non-finite PLC floating-point value");
            return n;
        }
        public static byte[] BatchRequest(Symbol[] symbols) {
            var bytes = new List<byte>();
            foreach (var s in symbols) {
                bytes.AddRange(BitConverter.GetBytes(s.Group)); bytes.AddRange(BitConverter.GetBytes(s.Offset));
                bytes.AddRange(BitConverter.GetBytes(s.Size));
            }
            return bytes.ToArray();
        }
        public static void DecodeBatch(Symbol[] symbols, Sample[] rows, byte[] data) {
            int total = symbols.Length * 4;
            foreach (var s in symbols) total = checked(total + (int)s.Size);
            if (data.Length != total) throw new Exception("Truncated/invalid ADS sum-read response");
            int offset = symbols.Length * 4;
            for (int i=0; i<symbols.Length; i++) {
                uint error = BitConverter.ToUInt32(data,i*4);
                byte[] value = new byte[symbols[i].Size];
                Array.Copy(data,offset,value,0,value.Length); offset += value.Length;
                if (error != 0) { rows[i].Error = "ADS " + error + " (0x" + error.ToString("X") + ")"; continue; }
                rows[i].RawHex = BitConverter.ToString(value).Replace("-", "");
                try { rows[i].Value = Decode(symbols[i],value); rows[i].Status = "ok"; }
                catch (NotSupportedException ex) { rows[i].Status = "unsupported"; rows[i].Error = ex.Message; }
                catch (Exception ex) { rows[i].Error = ex.Message; }
            }
        }
    }
    public sealed class Reader {
        readonly IReadTransport transport;
        public Reader(IReadTransport transport) { this.transport = transport; }
        public CatalogSnapshot Describe(string[] names, bool browse) {
            if (names == null || names.Length > 128) throw new Exception("At most 128 explicit symbols");
            var snapshot = new CatalogSnapshot();
            try {
                var state = transport.State(); snapshot.AdsState=state[0]; snapshot.DeviceState=state[1];
                snapshot.VersionBefore = transport.Version();
                if (browse) {
                    var browser = transport as ISymbolBrowser;
                    if (browser == null) throw new Exception("Transport does not support symbol browsing");
                    snapshot.Symbols=browser.ListSymbols();
                } else {
                    var symbols = new List<Symbol>();
                    foreach (var name in names) {
                        if (String.IsNullOrWhiteSpace(name) || name.Length>512 || name.IndexOf('\0')>=0) throw new Exception("Invalid symbol name");
                        try {
                            var symbol=transport.Lookup(name);
                            if (!String.Equals(symbol.Name,name,StringComparison.OrdinalIgnoreCase)) throw new Exception("Returned name mismatch");
                            symbols.Add(symbol);
                        } catch (Exception ex) { symbols.Add(new Symbol {Name=name,Error=ex.Message}); }
                    }
                    snapshot.Symbols=symbols.ToArray();
                }
                snapshot.VersionAfter=transport.Version();
                snapshot.VersionStable=snapshot.VersionBefore==snapshot.VersionAfter;
                if (!snapshot.VersionStable) throw new Exception("Symbol version changed during metadata query");
            } catch (Exception ex) { snapshot.Error=ex.Message; snapshot.Symbols=new Symbol[0]; }
            return snapshot;
        }
        public Snapshot Capture(string[] names) {
            if (names == null || names.Length < 1 || names.Length > 128) throw new Exception("Use 1..128 symbols");
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string name in names)
                if (String.IsNullOrWhiteSpace(name) || name.Length > 512 || name.IndexOf('\0') >= 0 || !seen.Add(name))
                    throw new Exception("Invalid or duplicate symbol name");
            var snap = new Snapshot { StartedUtc = DateTime.UtcNow.ToString("o"), Samples = new Sample[names.Length] };
            for (int i=0; i<names.Length; i++) snap.Samples[i] = new Sample { Name = names[i] };
            try {
                ushort[] state = transport.State(); snap.AdsState = state[0]; snap.DeviceState = state[1];
                snap.VersionBefore = transport.Version();
                var symbols = new List<Symbol>(); var rows = new List<Sample>();
                for (int i=0; i<names.Length; i++) {
                    try {
                        Symbol s = transport.Lookup(names[i]); snap.Samples[i].Type = s.Type;
                        if (!String.Equals(s.Name,names[i],StringComparison.OrdinalIgnoreCase))
                            throw new Exception("Returned symbol name does not match request");
                        if (s.Size < 1 || s.Size > 4096 || (s.Flags & 0x16) != 0)
                            throw new Exception("Unsupported bit-address, reference, interface, or oversized symbol");
                        symbols.Add(s); rows.Add(snap.Samples[i]);
                    } catch (Exception ex) { snap.Samples[i].Error = ex.Message; }
                }
                if (symbols.Count > 0) Codec.DecodeBatch(symbols.ToArray(), rows.ToArray(), transport.ReadBatch(symbols.ToArray()));
                snap.VersionAfter = transport.Version();
                snap.VersionStable = snap.VersionBefore == snap.VersionAfter;
                if (!snap.VersionStable) throw new Exception("Symbol version changed during capture; all values discarded, retry after engineering changes finish");
            } catch (Exception ex) {
                snap.Error = ex.Message;
                foreach (Sample row in snap.Samples) {
                    row.Status = "error"; row.Value = null; row.RawHex = null;
                    row.Error = ex.Message;
                }
            }
            snap.FinishedUtc = DateTime.UtcNow.ToString("o"); return snap;
        }
    }
    // Uses the installed Beckhoff DLL. No ADS write/write-control, route mutation,
    // handle allocation, notification registration, PLC method call or download API.
    public sealed class NativeTransport : IReadTransport, ISymbolBrowser {
        [StructLayout(LayoutKind.Sequential, Pack=1)]
        struct Address {
            [MarshalAs(UnmanagedType.ByValArray, SizeConst=6)] public byte[] NetId;
            public ushort Port;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr LoadLibraryEx(string file, IntPtr reserved, uint flags);
        [DllImport("kernel32.dll", CharSet=CharSet.Ansi, ExactSpelling=true)]
        static extern IntPtr GetProcAddress(IntPtr module, string name);
        [DllImport("kernel32.dll")] static extern bool FreeLibrary(IntPtr module);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int OpenFn();
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CloseFn(int port);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int TimeoutFn(int port, uint ms);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int StateFn(int port, ref Address addr, out ushort state, out ushort device);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ReadFn(int port, ref Address addr, uint group, uint offset, uint size, [Out] byte[] data, out uint actual);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int QueryFn(int port, ref Address addr, uint group, uint offset, uint size, [Out] byte[] data, uint requestSize, byte[] request, out uint actual);
        IntPtr module; int port; Address target;
        CloseFn close; ReadFn read; QueryFn query; StateFn state;
        T Bind<T>(string name) where T:class {
            IntPtr p = GetProcAddress(module,name);
            if (p == IntPtr.Zero) throw new Exception("ADS DLL missing export: " + name);
            return Marshal.GetDelegateForFunctionPointer(p,typeof(T)) as T;
        }
        static void Check(int code) {
            if (code != 0) throw new Exception("ADS " + code + " (0x" + code.ToString("X") + ")");
        }
        public NativeTransport(string dllPath, string netId, int adsPort, int timeoutMs) {
            if (adsPort < 851 || adsPort > 899) throw new Exception("Only PLC ADS ports 851..899 are supported");
            if (timeoutMs < 100 || timeoutMs > 5000) throw new Exception("Timeout must be 100..5000 ms");
            string[] parts = netId.Split('.'); if (parts.Length != 6) throw new Exception("AMS Net ID must have 6 bytes");
            target = new Address { NetId = new byte[6], Port = (ushort)adsPort };
            for (int i=0; i<6; i++) target.NetId[i] = Byte.Parse(parts[i],CultureInfo.InvariantCulture);
            if (!System.IO.Path.IsPathRooted(dllPath) || !System.IO.File.Exists(dllPath)) throw new Exception("ADS DLL absolute path missing");
            module = LoadLibraryEx(dllPath,IntPtr.Zero,0x1100);
            if (module == IntPtr.Zero) throw new Exception("Cannot load ADS DLL; Win32 " + Marshal.GetLastWin32Error());
            try {
                close = Bind<CloseFn>("AdsPortCloseEx"); read = Bind<ReadFn>("AdsSyncReadReqEx2");
                query = Bind<QueryFn>("AdsSyncReadWriteReqEx2"); state = Bind<StateFn>("AdsSyncReadStateReqEx");
                port = Bind<OpenFn>("AdsPortOpenEx")(); if (port == 0) throw new Exception("Local ADS router is unavailable");
                Check(Bind<TimeoutFn>("AdsSyncSetTimeoutEx")(port,(uint)timeoutMs));
            } catch { Dispose(); throw; }
        }
        public ushort[] State() {
            ushort a,d; Check(state(port,ref target,out a,out d)); return new ushort[] {a,d};
        }
        public uint Version() {
            byte[] b = new byte[4]; uint n;
            Check(read(port,ref target,0xF008,0,4,b,out n));
            if (n == 1) return b[0];
            if (n == 4) return BitConverter.ToUInt32(b,0);
            throw new Exception("Invalid symbol version length: " + n);
        }
        public Symbol[] ListSymbols() {
            byte[] info = new byte[8]; uint actual;
            Check(read(port,ref target,0xF00C,0,8,info,out actual));
            if (actual!=8) throw new Exception("Invalid symbol upload info length");
            uint count=BitConverter.ToUInt32(info,0), size=BitConverter.ToUInt32(info,4);
            if (count>100000 || size>16*1024*1024) throw new Exception("Symbol table exceeds read limit");
            if (size==0) return Codec.ParseSymbolTable(new byte[0],count);
            byte[] data=new byte[(int)size];
            Check(read(port,ref target,0xF00B,0,size,data,out actual));
            if (actual!=size) throw new Exception("Truncated symbol upload");
            return Codec.ParseSymbolTable(data,count);
        }
        // ADS ReadWrite here sends ONLY the symbol name as a query, never a PLC value.
        public Symbol Lookup(string name) {
            byte[] request = Encoding.UTF8.GetBytes(name + "\0"), b = new byte[65536]; uint n;
            Check(query(port,ref target,0xF009,0,(uint)b.Length,b,(uint)request.Length,request,out n));
            if (n > b.Length) throw new Exception("Invalid lookup response size");
            Array.Resize(ref b,(int)n); return Codec.ParseSymbol(b);
        }
        // 0xF080 is SUMUP_READ: request bytes contain addresses and lengths only.
        public byte[] ReadBatch(Symbol[] symbols) {
            int size = symbols.Length*4; foreach (Symbol s in symbols) size = checked(size+(int)s.Size);
            if (symbols.Length < 1 || symbols.Length > 128 || size > 524800) throw new Exception("Batch too large");
            byte[] b = new byte[size], request = Codec.BatchRequest(symbols); uint n;
            Check(query(port,ref target,0xF080,(uint)symbols.Length,(uint)b.Length,b,(uint)request.Length,request,out n));
            if (n != b.Length) throw new Exception("Truncated ADS batch read"); return b;
        }
        public void Dispose() {
            if (port != 0) { close(port); port=0; }
            if (module != IntPtr.Zero) { FreeLibrary(module); module=IntPtr.Zero; }
        }
    }
}
