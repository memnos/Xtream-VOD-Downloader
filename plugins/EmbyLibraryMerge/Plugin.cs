using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;

namespace EmbyLibraryMerge;

public class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
{
    public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
        : base(applicationPaths, xmlSerializer)
    {
        Instance = this;
    }

    public override string Name => "Library Merge";

    public override Guid Id => Guid.Parse("7c4e8f2a-9b1d-4e6a-8f3c-2d5a1b9e0c7f");

    public override string Description =>
        "Unisce film e serie duplicati (TMDB) tra cartelle locali e STRM.";

    public static Plugin Instance { get; private set; } = null!;

    public IEnumerable<PluginPageInfo> GetPages()
    {
        return
        [
            new PluginPageInfo
            {
                Name = "LibraryMerge",
                EmbeddedResourcePath = GetType().Namespace + ".Configuration.configPage.html",
            },
        ];
    }
}