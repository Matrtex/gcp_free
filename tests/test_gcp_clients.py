from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gcp_clients


class GcpClientsTestCase(unittest.TestCase):
    def tearDown(self):
        gcp_clients.projects_client.cache_clear()
        gcp_clients.instances_client.cache_clear()
        gcp_clients.images_client.cache_clear()
        gcp_clients.zones_client.cache_clear()
        gcp_clients.zone_operations_client.cache_clear()
        gcp_clients.global_operations_client.cache_clear()
        gcp_clients.firewalls_client.cache_clear()
        gcp_clients.disks_client.cache_clear()

    def test_instances_client_is_cached_within_process(self):
        created_clients = []

        def create_instance_client(**kwargs):
            client = SimpleNamespace(kwargs=kwargs, index=len(created_clients))
            created_clients.append(client)
            return client

        fake_compute = SimpleNamespace(InstancesClient=create_instance_client)
        with patch.object(gcp_clients, "compute_v1", fake_compute), patch.object(
            gcp_clients,
            "resourcemanager_v3",
            SimpleNamespace(),
        ), patch.object(gcp_clients, "google_exceptions", SimpleNamespace()):
            first_client = gcp_clients.instances_client()
            second_client = gcp_clients.instances_client()

        self.assertIs(first_client, second_client)
        self.assertEqual(len(created_clients), 1)
        self.assertEqual(first_client.kwargs["transport"], "rest")

    def test_clear_google_cloud_client_caches_resets_cached_instances_client(self):
        created_clients = []

        def create_instance_client(**kwargs):
            client = SimpleNamespace(kwargs=kwargs, index=len(created_clients))
            created_clients.append(client)
            return client

        fake_compute = SimpleNamespace(
            InstancesClient=create_instance_client,
            ImagesClient=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
            ZonesClient=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
            ZoneOperationsClient=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
            GlobalOperationsClient=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
            FirewallsClient=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
            DisksClient=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        )
        fake_resourcemanager = SimpleNamespace(
            ProjectsClient=lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        )

        with patch.object(gcp_clients, "compute_v1", fake_compute), patch.object(
            gcp_clients,
            "resourcemanager_v3",
            fake_resourcemanager,
        ), patch.object(gcp_clients, "google_exceptions", SimpleNamespace()):
            first_client = gcp_clients.instances_client()
            gcp_clients.clear_google_cloud_client_caches()
            second_client = gcp_clients.instances_client()

        self.assertIsNot(first_client, second_client)
        self.assertEqual(len(created_clients), 2)


if __name__ == "__main__":
    unittest.main()
