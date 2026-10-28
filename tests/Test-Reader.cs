namespace TwinCatReadOnly.Tests {
    sealed class Fake : IReadTransport, ISymbolBrowser {
        public string Mode = "ok"; int versions;
        public void Dispose() { }
        public ushort[] State() {
            if (Mode == "offline") throw new System.Exception("Simulated connection timeout");
            return new ushort[] {5,0};
        }
        public uint Version() { versions++; return (uint)(Mode == "changed" ? versions : 7); }
        public Symbol[] ListSymbols() { return new Symbol[] { Lookup("Demo.Value") }; }
        public Symbol Lookup(string name) {
            if (name == "Missing") throw new System.Exception("ADS 1808 (0x710)");
            return new Symbol { Name=name, Type=Mode == "unsupported" ? "STRUCT" : "REAL",
                DataType=Mode == "unsupported" ? 65u : 4u, Size=4, Group=0x4020, Offset=32,
                Flags=Mode == "reference" ? 4u : 0u };
        }
        public byte[] ReadBatch(Symbol[] symbols) {
            if (Mode == "truncated") return new byte[2];
            var b = new System.Collections.Generic.List<byte>();
            for(int i=0; i<symbols.Length; i++) b.AddRange(System.BitConverter.GetBytes((uint)(Mode == "item-error" && i == 0 ? 1808 : 0)));
            foreach(var s in symbols) b.AddRange(System.BitConverter.GetBytes(10.5f));
            return b.ToArray();
        }
    }
    public static class Checks {
        static void Assert(bool ok, string message) { if (!ok) throw new System.Exception(message); }
        static Snapshot Capture(string mode, params string[] names) { return new Reader(new Fake {Mode=mode}).Capture(names); }
        public static string[] Run() {
            var passed = new System.Collections.Generic.List<string>();
            var snap = Capture("ok","Demo.Value");
            Assert(snap.VersionStable && snap.AdsState==5 && snap.Samples[0].Status=="ok" && (double)snap.Samples[0].Value==10.5, "Valid snapshot");
            passed.Add("valid snapshot, state and version");
            snap=Capture("ok","Missing","Demo.Value");
            Assert(snap.Samples[0].Value==null && snap.Samples[0].Status=="error" && snap.Samples[1].Status=="ok", "Missing symbol isolation");
            passed.Add("missing symbol remains unavailable, other values survive");
            snap=Capture("changed","Demo.Value");
            Assert(!snap.VersionStable && snap.Samples[0].Value==null && snap.Samples[0].RawHex==null && snap.Error!=null, "Changed symbol version");
            passed.Add("online change discards stale-address data");
            snap=Capture("offline","Demo.Value");
            Assert(snap.AdsState==-1 && snap.Samples[0].Value==null && snap.Error!=null,"Offline state");
            passed.Add("connection failure cannot look like zero or false");
            snap=Capture("truncated","Demo.Value");
            Assert(snap.Error!=null && snap.Samples[0].Value==null,"Truncated batch");
            passed.Add("truncated batch rejected");
            snap=Capture("item-error","Demo.A","Demo.B");
            Assert(snap.Samples[0].Value==null && snap.Samples[1].Status=="ok","Per-item ADS error");
            passed.Add("per-symbol ADS error preserves successful neighbors");
            snap=Capture("reference","Demo.Value");
            Assert(snap.Samples[0].Status=="error" && snap.Samples[0].Value==null,"Reference guard");
            snap=Capture("unsupported","Demo.Value");
            Assert(snap.Samples[0].Status=="unsupported" && snap.Samples[0].Value==null && snap.Samples[0].RawHex!=null,"Unknown layout");
            passed.Add("reference and unknown layouts never guessed");
            bool failed=false;
            try { Capture("ok","Demo.A","demo.a"); } catch { failed=true; }
            Assert(failed,"Duplicate symbol validation");
            passed.Add("case-insensitive duplicate input rejected");
            var symbol = new Symbol {Name="test",Type="INT",DataType=2,Size=2};
            Assert((short)Codec.Decode(symbol,System.BitConverter.GetBytes((short)-123))==-123,"Signed INT");
            symbol.Type="BOOL"; symbol.DataType=33; symbol.Size=1;
            Assert((bool)Codec.Decode(symbol,new byte[]{1}) && !(bool)Codec.Decode(symbol,new byte[]{0}),"BOOL");
            symbol.Type="UINT"; symbol.DataType=18; symbol.Size=2;
            Assert((ushort)Codec.Decode(symbol,System.BitConverter.GetBytes((ushort)65535))==65535,"UINT");
            symbol.Type="LREAL"; symbol.DataType=5; symbol.Size=8;
            Assert((double)Codec.Decode(symbol,System.BitConverter.GetBytes(-0.25))==-0.25,"LREAL");
            failed=false; try { Codec.Decode(symbol,System.BitConverter.GetBytes(double.NaN)); } catch { failed=true; }
            Assert(failed,"NaN should not be normal numeric data");
            passed.Add("BOOL, signed/unsigned integers, REAL/LREAL, non-finite guard");
            byte[] request=Codec.BatchRequest(new Symbol[]{new Symbol{Group=0x4020,Offset=123,Size=4}});
            Assert(request.Length==12 && System.BitConverter.ToUInt32(request,0)==0x4020 && System.BitConverter.ToUInt32(request,4)==123 && System.BitConverter.ToUInt32(request,8)==4,"Address-only batch request");
            passed.Add("sum-read request contains only addresses and sizes");
            var entry=new System.Collections.Generic.List<byte>();
            entry.AddRange(System.BitConverter.GetBytes(40u)); entry.AddRange(System.BitConverter.GetBytes(0x4020u));
            entry.AddRange(System.BitConverter.GetBytes(8u)); entry.AddRange(System.BitConverter.GetBytes(4u));
            entry.AddRange(System.BitConverter.GetBytes(4u)); entry.AddRange(System.BitConverter.GetBytes(0u));
            entry.AddRange(System.BitConverter.GetBytes((ushort)3)); entry.AddRange(System.BitConverter.GetBytes((ushort)4));
            entry.AddRange(System.BitConverter.GetBytes((ushort)0)); entry.AddRange(System.Text.Encoding.ASCII.GetBytes("D.X\0REAL\0\0"));
            symbol=Codec.ParseSymbol(entry.ToArray());
            Assert(symbol.Name=="D.X" && symbol.Type=="REAL" && symbol.Size==4,"Packed symbol entry parse");
            failed=false; try { Codec.ParseSymbol(new byte[29]); } catch { failed=true; }
            Assert(failed,"Truncated symbol entry");
            passed.Add("30-byte packed metadata layout and truncation validation");
            var table=Codec.ParseSymbolTable(entry.ToArray(),1);
            Assert(table.Length==1 && table[0].Name=="D.X" && table[0].Comment=="", "Symbol upload table");
            foreach (uint count in new uint[]{0,2,100001}) {
                failed=false; try { Codec.ParseSymbolTable(entry.ToArray(),count); } catch { failed=true; }
                Assert(failed,"Symbol count/size mismatch must fail");
            }
            byte[] broken=entry.ToArray(); broken[broken.Length-1]=1;
            failed=false; try { Codec.ParseSymbolTable(broken,1); } catch { failed=true; }
            Assert(failed,"Comment terminator validation");
            passed.Add("symbol upload count, bounds and terminator validation");
            var metadata=new Reader(new Fake()).Describe(new string[]{"Missing","Demo.Value"},false);
            Assert(metadata.VersionStable && metadata.Error==null && metadata.Symbols[0].Error!=null && metadata.Symbols[1].Name=="Demo.Value", "Metadata per-entry failure");
            metadata=new Reader(new Fake()).Describe(new string[0],true);
            Assert(metadata.VersionStable && metadata.Symbols.Length==1,"Metadata browsing");
            metadata=new Reader(new Fake {Mode="changed"}).Describe(new string[0],true);
            Assert(!metadata.VersionStable && metadata.Error!=null && metadata.Symbols.Length==0,"Unstable metadata discarded");
            metadata=new Reader(new Fake {Mode="offline"}).Describe(new string[0],false);
            Assert(metadata.Error!=null && metadata.AdsState==-1,"State query failure");
            passed.Add("metadata browsing, per-entry errors, unstable version and offline state");
            return passed.ToArray();
        }
    }
}
