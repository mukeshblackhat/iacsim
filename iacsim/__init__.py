"""iacsim — Infrastructure-as-Code latency simulator.

Pipeline (each arrow is a plug-and-play extension point, see core/interfaces.py):

    IaC files ─parser─► RawResources ─normaliser─► InfraGraph ─inference─► InfraGraph(+edges)
        ─latency rules─► InfraGraph(+costs) ─walker─► Result ─analyzers─► Findings ─reporter─► output
"""

__version__ = "0.1.0"
