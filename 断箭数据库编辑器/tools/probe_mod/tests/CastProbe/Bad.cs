using System.Collections;

namespace CastProbe
{
    /// <summary>
    /// POSITIVE CONTROL for tools/check_no_il2cpp_ienumerable_cast.ps1.
    ///
    /// This assembly deliberately contains the exact pattern that made all 24 tables of the first
    /// real probe run come back empty: casting an Il2Cpp proxy collection to the managed
    /// System.Collections.IEnumerable. The ruler must FLAG this file, otherwise it cannot go red
    /// and its PASS on the real mod would mean nothing.
    ///
    /// Never referenced by the mod. Never deployed.
    /// </summary>
    public static class BadCastProbe
    {
        public static int CountViaForbiddenCast(object o)
        {
            var e = o as IEnumerable;
            if (e == null) return -1;
            int n = 0;
            foreach (object _ in e) n++;
            return n;
        }

        public static int CountViaForbiddenCastClass(object o)
        {
            var e = (IEnumerable)o;
            int n = 0;
            foreach (object _ in e) n++;
            return n;
        }
    }
}
