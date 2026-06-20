using MediaBrowser.Common.Plugins;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Model.Logging;

namespace EmbyLibraryMerge;

/// <summary>Confirms the plugin assembly loaded at server startup.</summary>
public class PluginEntryPoint : IServerEntryPoint
{
    private readonly ILogger _logger;

    public PluginEntryPoint(ILogger logger)
    {
        _logger = logger;
    }

    public void Run()
    {
        _logger.Info("Library Merge plugin loaded (film + serie)");
    }

    public Task RunAsync() => Task.CompletedTask;

    public void Dispose()
    {
    }
}
