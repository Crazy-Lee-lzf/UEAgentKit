#include "PieRuntimeInspection.h"

#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "Components/ActorComponent.h"
#include "Components/MeshComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/Engine.h"
#include "Engine/GameInstance.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "GameFramework/Pawn.h"
#include "Materials/MaterialInterface.h"
#include "UObject/Package.h"

namespace UEAgentKitPieRuntime
{
	namespace
	{
		FString NetModeWireName(const ENetMode NetMode)
		{
			switch (NetMode)
			{
			case NM_Standalone:
				return TEXT("standalone");
			case NM_DedicatedServer:
				return TEXT("dedicatedServer");
			case NM_ListenServer:
				return TEXT("listenServer");
			case NM_Client:
				return TEXT("client");
			default:
				return TEXT("unknown");
			}
		}

		FString NetModeRoleName(const ENetMode NetMode)
		{
			if (NetMode == NM_Standalone)
			{
				return TEXT("standalone");
			}
			return NetMode == NM_Client ? TEXT("client") : TEXT("server");
		}

		FString NetRoleEnumName(const ENetRole Role)
		{
			const UEnum* RoleEnum = StaticEnum<ENetRole>();
			if (RoleEnum == nullptr)
			{
				return FString();
			}
			return RoleEnum->GetNameStringByValue(static_cast<int64>(Role));
		}

		FString NetRoleShortName(const ENetRole Role)
		{
			FString Name = NetRoleEnumName(Role);
			if (Name.StartsWith(TEXT("ROLE_"), ESearchCase::CaseSensitive))
			{
				Name.RightChopInline(5);
			}
			return Name.ToLower();
		}

		FString CompactGuidDigits(const FGuid& Guid)
		{
			return Guid.ToString(EGuidFormats::Digits).ToLower();
		}

		FString NormalizeGuidText(const FString& Text)
		{
			FString Compact = Text;
			Compact.ReplaceInline(TEXT("-"), TEXT(""));
			return Compact.ToLower();
		}

		TSharedRef<FJsonObject> VectorJson(const FVector& Vector)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetNumberField(TEXT("x"), Vector.X);
			Item->SetNumberField(TEXT("y"), Vector.Y);
			Item->SetNumberField(TEXT("z"), Vector.Z);
			return Item;
		}

		TSharedRef<FJsonObject> RotatorJson(const FRotator& Rotator)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetNumberField(TEXT("pitch"), Rotator.Pitch);
			Item->SetNumberField(TEXT("yaw"), Rotator.Yaw);
			Item->SetNumberField(TEXT("roll"), Rotator.Roll);
			return Item;
		}

		/**
		 * Game-thread-only observation state.  The Editor Bridge pumps every
		 * request on the game thread, so no locking is introduced here.
		 */
		struct FPieInstanceHistory
		{
			TSet<int32> LiveInstanceIds;
			TArray<int32> RetiredInstanceIds;
		};

		FPieInstanceHistory& GetPieInstanceHistory()
		{
			static FPieInstanceHistory History;
			return History;
		}

		bool PathEqualsExact(const FString& Left, const FString& Right)
		{
			return Left.Equals(Right, ESearchCase::CaseSensitive);
		}

		FString JoinCandidatePaths(const TArray<FString>& Candidates)
		{
			FString Joined;
			for (const FString& Candidate : Candidates)
			{
				if (!Joined.IsEmpty())
				{
					Joined += TEXT(", ");
				}
				Joined += Candidate;
			}
			return Joined;
		}
	}

	const TCHAR* LookupStatusCode(const ELookupStatus Status)
	{
		switch (Status)
		{
		case ELookupStatus::NotRunning:
			return TEXT("pie-not-running");
		case ELookupStatus::NotFound:
			return TEXT("pie-instance-not-found");
		case ELookupStatus::Stale:
			return TEXT("pie-instance-stale");
		case ELookupStatus::Matched:
		default:
			return TEXT("");
		}
	}

	const TCHAR* FailureCode(const EFailure Failure)
	{
		switch (Failure)
		{
		case EFailure::InvalidRequest:
			return TEXT("pie-invalid-parameters");
		case EFailure::InstanceNotRunning:
			return TEXT("pie-not-running");
		case EFailure::InstanceNotFound:
			return TEXT("pie-instance-not-found");
		case EFailure::InstanceStale:
			return TEXT("pie-instance-stale");
		case EFailure::WorldStale:
			return TEXT("pie-world-stale");
		case EFailure::ActorNotFound:
			return TEXT("pie-actor-not-found");
		case EFailure::ActorAmbiguous:
			return TEXT("pie-actor-ambiguous");
		case EFailure::ActorStale:
			return TEXT("pie-actor-stale");
		case EFailure::ContextScanTruncated:
			return TEXT("pie-context-scan-truncated");
		case EFailure::ScanTruncated:
			return TEXT("pie-actor-scan-truncated");
		case EFailure::None:
		default:
			return TEXT("");
		}
	}

	bool IsStaleFailure(const EFailure Failure)
	{
		return Failure == EFailure::InstanceStale
			|| Failure == EFailure::WorldStale
			|| Failure == EFailure::ActorStale;
	}

	ELookupStatus ClassifyInstanceLookup(
		const int32 RequestedInstanceId,
		const TArray<int32>& LiveInstanceIds,
		const TArray<int32>& RetiredInstanceIds)
	{
		if (RequestedInstanceId >= 0 && LiveInstanceIds.Contains(RequestedInstanceId))
		{
			return ELookupStatus::Matched;
		}
		if (RequestedInstanceId >= 0 && RetiredInstanceIds.Contains(RequestedInstanceId))
		{
			return ELookupStatus::Stale;
		}
		if (LiveInstanceIds.Num() == 0)
		{
			return ELookupStatus::NotRunning;
		}
		return ELookupStatus::NotFound;
	}

	void ObserveLivePieInstances(const TArray<int32>& LiveInstanceIds)
	{
		FPieInstanceHistory& History = GetPieInstanceHistory();
		TSet<int32> NewLiveInstanceIds;
		for (const int32 InstanceId : LiveInstanceIds)
		{
			if (InstanceId >= 0)
			{
				NewLiveInstanceIds.Add(InstanceId);
			}
		}
		for (const int32 PreviousInstanceId : History.LiveInstanceIds)
		{
			if (!NewLiveInstanceIds.Contains(PreviousInstanceId))
			{
				History.RetiredInstanceIds.Remove(PreviousInstanceId);
				History.RetiredInstanceIds.Add(PreviousInstanceId);
			}
		}
		while (History.RetiredInstanceIds.Num() > RetiredPieInstanceHistory)
		{
			History.RetiredInstanceIds.RemoveAt(0);
		}
		History.LiveInstanceIds = MoveTemp(NewLiveInstanceIds);
	}

	void GetRetiredPieInstances(TArray<int32>& OutRetiredInstanceIds)
	{
		OutRetiredInstanceIds = GetPieInstanceHistory().RetiredInstanceIds;
	}

	void ResetPieInstanceHistoryForTests()
	{
		FPieInstanceHistory& History = GetPieInstanceHistory();
		History.LiveInstanceIds.Reset();
		History.RetiredInstanceIds.Reset();
	}

	void CollectPieWorldContexts(TArray<FWorldContextSnapshot>& OutContexts, bool& bOutTruncated)
	{
		OutContexts.Reset();
		bOutTruncated = false;
		TArray<int32> LiveInstanceIds;
		if (GEditor == nullptr)
		{
			ObserveLivePieInstances(LiveInstanceIds);
			return;
		}

		const TIndirectArray<FWorldContext>& WorldContexts = GEditor->GetWorldContexts();
		int32 ContextIndex = 0;
		for (const FWorldContext& Context : WorldContexts)
		{
			const int32 CurrentIndex = ContextIndex++;
			if (Context.WorldType != EWorldType::PIE)
			{
				continue;
			}
			UWorld* World = Context.World();
			if (World == nullptr)
			{
				continue;
			}
			if (OutContexts.Num() >= MaxWorldContextScan)
			{
				bOutTruncated = true;
				break;
			}

			FWorldContextSnapshot Snapshot;
			Snapshot.World = World;
			Snapshot.ContextIndex = CurrentIndex;
			Snapshot.PieInstanceId = Context.PIEInstance;
			if (Snapshot.PieInstanceId < 0 && World->GetOutermost() != nullptr)
			{
				Snapshot.PieInstanceId = World->GetOutermost()->GetPIEInstanceID();
			}
			const ENetMode NetMode = World->GetNetMode();
			Snapshot.NetMode = static_cast<int32>(NetMode);
			Snapshot.NetModeName = NetModeWireName(NetMode);
			Snapshot.ServerOrClient = NetModeRoleName(NetMode);
			Snapshot.bIsServer = Snapshot.ServerOrClient == TEXT("server");
			Snapshot.bIsClient = Snapshot.ServerOrClient == TEXT("client");
			Snapshot.bIsStandalone = NetMode == NM_Standalone;
			Snapshot.bIsDedicatedServer = NetMode == NM_DedicatedServer;
			Snapshot.bIsListenServer = NetMode == NM_ListenServer;
			Snapshot.bIsPrimaryPieInstance = Context.bIsPrimaryPIEInstance;
			Snapshot.bRunAsDedicated = Context.RunAsDedicated;
			Snapshot.WorldPath = World->GetPathName();
			Snapshot.WorldName = World->GetMapName();
			Snapshot.PackagePath = World->GetOutermost() != nullptr ? World->GetOutermost()->GetName() : FString();
			Snapshot.WorldType = UEAgentKitEditorBridgePrivate::GetWorldTypeName(World->WorldType);
			Snapshot.ContextId = FString::Printf(
				TEXT("pie-%d-%s"),
				Snapshot.PieInstanceId,
				*Snapshot.NetModeName);
			Snapshot.bHasBegunPlay = World->HasBegunPlay();
			Snapshot.bIsPaused = World->IsPaused();
			Snapshot.TimeSeconds = World->GetTimeSeconds();
			Snapshot.LocalPlayerCount = World->GetGameInstance() != nullptr
				? World->GetGameInstance()->GetNumLocalPlayers()
				: 0;

			LiveInstanceIds.AddUnique(Snapshot.PieInstanceId);
			OutContexts.Add(MoveTemp(Snapshot));
		}
		ObserveLivePieInstances(LiveInstanceIds);
	}

	TSharedRef<FJsonObject> DescribeWorldContext(const FWorldContextSnapshot& Snapshot)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("contextId"), Snapshot.ContextId);
		Item->SetNumberField(TEXT("pieInstanceId"), Snapshot.PieInstanceId);
		Item->SetNumberField(TEXT("worldContextIndex"), Snapshot.ContextIndex);
		Item->SetStringField(TEXT("worldPath"), Snapshot.WorldPath);
		Item->SetStringField(TEXT("worldName"), Snapshot.WorldName);
		Item->SetStringField(TEXT("packagePath"), Snapshot.PackagePath);
		Item->SetStringField(TEXT("worldType"), Snapshot.WorldType);
		Item->SetStringField(TEXT("netModeName"), Snapshot.NetModeName);
		Item->SetNumberField(TEXT("netMode"), Snapshot.NetMode);
		Item->SetStringField(TEXT("serverOrClient"), Snapshot.ServerOrClient);
		Item->SetBoolField(TEXT("isServer"), Snapshot.bIsServer);
		Item->SetBoolField(TEXT("isClient"), Snapshot.bIsClient);
		Item->SetBoolField(TEXT("isStandalone"), Snapshot.bIsStandalone);
		Item->SetBoolField(TEXT("isDedicatedServer"), Snapshot.bIsDedicatedServer);
		Item->SetBoolField(TEXT("isListenServer"), Snapshot.bIsListenServer);
		Item->SetBoolField(TEXT("isPrimaryPieInstance"), Snapshot.bIsPrimaryPieInstance);
		Item->SetBoolField(TEXT("runAsDedicated"), Snapshot.bRunAsDedicated);
		Item->SetNumberField(TEXT("localPlayerCount"), Snapshot.LocalPlayerCount);
		Item->SetBoolField(TEXT("hasBegunPlay"), Snapshot.bHasBegunPlay);
		Item->SetBoolField(TEXT("isPaused"), Snapshot.bIsPaused);
		Item->SetNumberField(TEXT("timeSeconds"), Snapshot.TimeSeconds);
		return Item;
	}

	TSharedRef<FJsonObject> BuildWorldContextsJson(const int32 MaxContexts)
	{
		const int32 EffectiveMax = FMath::Clamp(MaxContexts, 1, MaxPieWorldContexts);
		TArray<FWorldContextSnapshot> Contexts;
		bool bScanTruncated = false;
		CollectPieWorldContexts(Contexts, bScanTruncated);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		const FString PieState = UEAgentKitEditorBridgePrivate::GetPieStateName();
		const bool bRunning = Contexts.Num() > 0;
		Result->SetStringField(TEXT("state"), PieState);
		Result->SetBoolField(TEXT("running"), bRunning);
		Result->SetBoolField(TEXT("simulatingInEditor"), PieState.Equals(TEXT("simulating"), ESearchCase::CaseSensitive));
		Result->SetBoolField(TEXT("readOnly"), true);
		Result->SetBoolField(TEXT("requiresDatabase"), false);
		Result->SetStringField(
			TEXT("code"),
			bRunning ? FString() : FString(LookupStatusCode(ELookupStatus::NotRunning)));
		Result->SetNumberField(TEXT("maxContexts"), EffectiveMax);
		Result->SetNumberField(TEXT("contextCount"), Contexts.Num());

		int32 ServerCount = 0;
		int32 ClientCount = 0;
		int32 StandaloneCount = 0;
		TArray<TSharedPtr<FJsonValue>> Items;
		TArray<TSharedPtr<FJsonValue>> InstanceIds;
		TSet<int32> SeenInstanceIds;
		for (const FWorldContextSnapshot& Snapshot : Contexts)
		{
			if (Snapshot.bIsServer)
			{
				++ServerCount;
			}
			else if (Snapshot.bIsClient)
			{
				++ClientCount;
			}
			else
			{
				++StandaloneCount;
			}
			if (!SeenInstanceIds.Contains(Snapshot.PieInstanceId))
			{
				SeenInstanceIds.Add(Snapshot.PieInstanceId);
				InstanceIds.Add(MakeShared<FJsonValueNumber>(Snapshot.PieInstanceId));
			}
			if (Items.Num() < EffectiveMax)
			{
				Items.Add(MakeShared<FJsonValueObject>(DescribeWorldContext(Snapshot)));
			}
		}

		Result->SetNumberField(TEXT("serverCount"), ServerCount);
		Result->SetNumberField(TEXT("clientCount"), ClientCount);
		Result->SetNumberField(TEXT("standaloneCount"), StandaloneCount);
		Result->SetNumberField(TEXT("returnedCount"), Items.Num());
		Result->SetBoolField(TEXT("truncated"), bScanTruncated || Contexts.Num() > Items.Num());
		Result->SetBoolField(TEXT("identitiesUnique"), Contexts.Num() == SeenInstanceIds.Num());
		Result->SetArrayField(TEXT("pieInstanceIds"), InstanceIds);
		Result->SetArrayField(TEXT("items"), Items);
		return Result;
	}

	namespace
	{
		TSharedRef<FJsonObject> DescribeActor(
			const FWorldContextSnapshot& Snapshot,
			AActor* Actor,
			const FActorInspectionRequest& Request,
			const TCHAR* ResolvedBy)
		{
			const int32 MaxComponents = FMath::Clamp(Request.MaxComponents, 1, MaxActorComponents);
			const int32 MaxAssets = FMath::Clamp(Request.MaxComponentAssets, 1, MaxComponentAssets);

			TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
			Result->SetBoolField(TEXT("readOnly"), true);
			Result->SetBoolField(TEXT("requiresDatabase"), false);
			Result->SetStringField(TEXT("resolvedBy"), ResolvedBy);
			Result->SetStringField(TEXT("pieState"), UEAgentKitEditorBridgePrivate::GetPieStateName());
			Result->SetNumberField(TEXT("pieInstanceId"), Snapshot.PieInstanceId);
			Result->SetStringField(TEXT("contextId"), Snapshot.ContextId);
			Result->SetStringField(TEXT("worldPath"), Snapshot.WorldPath);
			Result->SetStringField(TEXT("worldName"), Snapshot.WorldName);
			Result->SetStringField(TEXT("netModeName"), Snapshot.NetModeName);
			Result->SetNumberField(TEXT("netMode"), Snapshot.NetMode);
			Result->SetStringField(TEXT("serverOrClient"), Snapshot.ServerOrClient);

			Result->SetStringField(TEXT("actorPath"), Actor->GetPathName());
			Result->SetStringField(TEXT("actorName"), Actor->GetName());
			Result->SetStringField(TEXT("actorLabel"), Actor->GetActorLabel(false));
			Result->SetStringField(
				TEXT("classPath"),
				Actor->GetClass() != nullptr ? Actor->GetClass()->GetPathName() : FString());
			Result->SetStringField(
				TEXT("levelPath"),
				Actor->GetLevel() != nullptr ? Actor->GetLevel()->GetPathName() : FString());
			Result->SetStringField(
				TEXT("packagePath"),
				Actor->GetOutermost() != nullptr ? Actor->GetOutermost()->GetName() : FString());
			Result->SetStringField(TEXT("actorGuid"), Actor->GetActorGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
			Result->SetStringField(
				TEXT("actorInstanceGuid"),
				Actor->GetActorInstanceGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
			Result->SetStringField(
				TEXT("ownerPath"),
				Actor->GetOwner() != nullptr ? Actor->GetOwner()->GetPathName() : FString());
			Result->SetStringField(
				TEXT("instigatorPath"),
				Actor->GetInstigator() != nullptr ? Actor->GetInstigator()->GetPathName() : FString());

			TArray<TSharedPtr<FJsonValue>> Hierarchy;
			for (const UClass* Class = Actor->GetClass();
				Class != nullptr && Hierarchy.Num() < MaxClassHierarchy;
				Class = Class->GetSuperClass())
			{
				Hierarchy.Add(MakeShared<FJsonValueString>(Class->GetPathName()));
			}
			Result->SetArrayField(TEXT("classHierarchy"), Hierarchy);

			const FTransform Transform = Actor->GetActorTransform();
			TSharedRef<FJsonObject> TransformJson = MakeShared<FJsonObject>();
			TransformJson->SetObjectField(TEXT("location"), VectorJson(Transform.GetLocation()));
			TransformJson->SetObjectField(TEXT("rotation"), RotatorJson(Transform.Rotator()));
			TransformJson->SetObjectField(TEXT("scale"), VectorJson(Transform.GetScale3D()));
			Result->SetObjectField(TEXT("transform"), TransformJson);

			const ENetRole LocalRole = Actor->GetLocalRole();
			const ENetRole RemoteRole = Actor->GetRemoteRole();
			TSharedRef<FJsonObject> NetRoleJson = MakeShared<FJsonObject>();
			NetRoleJson->SetStringField(TEXT("localRole"), NetRoleEnumName(LocalRole));
			NetRoleJson->SetStringField(TEXT("localRoleName"), NetRoleShortName(LocalRole));
			NetRoleJson->SetStringField(TEXT("remoteRole"), NetRoleEnumName(RemoteRole));
			NetRoleJson->SetStringField(TEXT("remoteRoleName"), NetRoleShortName(RemoteRole));
			NetRoleJson->SetBoolField(TEXT("hasAuthority"), Actor->HasAuthority());
			NetRoleJson->SetStringField(TEXT("netModeName"), Snapshot.NetModeName);
			NetRoleJson->SetStringField(TEXT("serverOrClient"), Snapshot.ServerOrClient);
			NetRoleJson->SetBoolField(TEXT("isServer"), Snapshot.bIsServer);
			NetRoleJson->SetBoolField(TEXT("isClient"), Snapshot.bIsClient);
			Result->SetObjectField(TEXT("netRole"), NetRoleJson);

			TArray<UActorComponent*> Components;
			Actor->GetComponents(Components);
			// TArray::Sort dereferences pointers, so the predicate receives components.
			Components.Sort([](const UActorComponent& Left, const UActorComponent& Right)
			{
				return Left.GetName() < Right.GetName();
			});

			TArray<TSharedPtr<FJsonValue>> ComponentItems;
			for (UActorComponent* Component : Components)
			{
				if (Component == nullptr || ComponentItems.Num() >= MaxComponents)
				{
					continue;
				}
				TArray<FString> Assets;
				bool bAssetsTruncated = false;
				auto AddAssetPath = [&Assets, &bAssetsTruncated, MaxAssets](const FString& AssetPath)
				{
					if (AssetPath.IsEmpty() || Assets.Contains(AssetPath))
					{
						return;
					}
					if (Assets.Num() >= MaxAssets)
					{
						bAssetsTruncated = true;
						return;
					}
					Assets.Add(AssetPath);
				};

				if (const UStaticMeshComponent* StaticMeshComponent = Cast<UStaticMeshComponent>(Component))
				{
					if (const UStaticMesh* StaticMesh = StaticMeshComponent->GetStaticMesh())
					{
						AddAssetPath(StaticMesh->GetPathName());
					}
				}
				if (const USkeletalMeshComponent* SkeletalMeshComponent = Cast<USkeletalMeshComponent>(Component))
				{
					if (const USkeletalMesh* SkeletalMesh = SkeletalMeshComponent->GetSkeletalMeshAsset())
					{
						AddAssetPath(SkeletalMesh->GetPathName());
					}
				}
				if (const UMeshComponent* MeshComponent = Cast<UMeshComponent>(Component))
				{
					const int32 MaterialCount = MeshComponent->GetNumMaterials();
					for (int32 Index = 0; Index < MaterialCount && !bAssetsTruncated; ++Index)
					{
						if (const UMaterialInterface* Material = MeshComponent->GetMaterial(Index))
						{
							AddAssetPath(Material->GetPathName());
						}
					}
				}

				TSharedRef<FJsonObject> ComponentJson = MakeShared<FJsonObject>();
				ComponentJson->SetStringField(TEXT("name"), Component->GetName());
				ComponentJson->SetStringField(TEXT("objectPath"), Component->GetPathName());
				ComponentJson->SetStringField(
					TEXT("classPath"),
					Component->GetClass() != nullptr ? Component->GetClass()->GetPathName() : FString());
				ComponentJson->SetBoolField(TEXT("isRegistered"), Component->IsRegistered());
				ComponentJson->SetBoolField(TEXT("isActive"), Component->IsActive());
				ComponentJson->SetBoolField(TEXT("assetsTruncated"), bAssetsTruncated);
				TArray<TSharedPtr<FJsonValue>> AssetValues;
				for (const FString& AssetPath : Assets)
				{
					AssetValues.Add(MakeShared<FJsonValueString>(AssetPath));
				}
				ComponentJson->SetArrayField(TEXT("assets"), AssetValues);
				ComponentItems.Add(MakeShared<FJsonValueObject>(ComponentJson));
			}

			Result->SetNumberField(TEXT("componentCount"), Components.Num());
			Result->SetNumberField(TEXT("componentsReturned"), ComponentItems.Num());
			Result->SetBoolField(TEXT("componentsTruncated"), Components.Num() > ComponentItems.Num());
			Result->SetNumberField(TEXT("maxComponents"), MaxComponents);
			Result->SetNumberField(TEXT("maxComponentAssets"), MaxAssets);
			Result->SetArrayField(TEXT("components"), ComponentItems);
			return Result;
		}
	}

	bool TryDescribePieActor(
		const FActorInspectionRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutResult.Reset();
		OutErrorCode.Reset();
		OutErrorMessage.Reset();

		auto Fail = [&OutErrorCode, &OutErrorMessage](const EFailure Failure, const FString& Message)
		{
			OutErrorCode = FailureCode(Failure);
			OutErrorMessage = Message;
			return false;
		};

		if (Request.PieInstanceId < 0)
		{
			return Fail(
				EFailure::InvalidRequest,
				TEXT("pieInstanceId must be the exact PIE instance id reported by editor.getPieWorldContexts (0 is the primary instance)."));
		}
		int32 IdentifierCount = 0;
		IdentifierCount += Request.ActorPath.IsEmpty() ? 0 : 1;
		IdentifierCount += Request.ActorGuid.IsEmpty() ? 0 : 1;
		IdentifierCount += Request.ActorName.IsEmpty() ? 0 : 1;
		IdentifierCount += Request.ActorLabel.IsEmpty() ? 0 : 1;
		if (IdentifierCount != 1)
		{
			return Fail(
				EFailure::InvalidRequest,
				TEXT("Provide exactly one Actor identity: actorPath, actorGuid, actorName, or actorLabel."));
		}

		TArray<FWorldContextSnapshot> Contexts;
		bool bContextScanTruncated = false;
		CollectPieWorldContexts(Contexts, bContextScanTruncated);
		if (bContextScanTruncated)
		{
			return Fail(
				EFailure::ContextScanTruncated,
				FString::Printf(
					TEXT("The PIE world context scan reached its bounded limit of %d contexts, so the requested instance could not be verified."),
					MaxWorldContextScan));
		}

		TArray<int32> LiveInstanceIds;
		for (const FWorldContextSnapshot& Snapshot : Contexts)
		{
			LiveInstanceIds.AddUnique(Snapshot.PieInstanceId);
		}
		TArray<int32> RetiredInstanceIds;
		GetRetiredPieInstances(RetiredInstanceIds);

		const ELookupStatus Status = ClassifyInstanceLookup(Request.PieInstanceId, LiveInstanceIds, RetiredInstanceIds);
		if (Status == ELookupStatus::NotRunning)
		{
			return Fail(
				EFailure::InstanceNotRunning,
				FString::Printf(
					TEXT("PIE is not running in this Editor process, so PIE instance %d has no live world."),
					Request.PieInstanceId));
		}
		if (Status == ELookupStatus::Stale)
		{
			return Fail(
				EFailure::InstanceStale,
				FString::Printf(
					TEXT("PIE instance %d was observed earlier in this Editor process but is no longer running; re-query editor.getPieWorldContexts."),
					Request.PieInstanceId));
		}
		if (Status == ELookupStatus::NotFound)
		{
			return Fail(
				EFailure::InstanceNotFound,
				FString::Printf(
					TEXT("PIE instance %d is not one of the live PIE world contexts; re-query editor.getPieWorldContexts."),
					Request.PieInstanceId));
		}

		const FWorldContextSnapshot* Snapshot = Contexts.FindByPredicate(
			[&Request](const FWorldContextSnapshot& Candidate)
			{
				return Candidate.PieInstanceId == Request.PieInstanceId;
			});
		if (Snapshot == nullptr || Snapshot->World == nullptr)
		{
			return Fail(
				EFailure::InstanceNotFound,
				FString::Printf(TEXT("PIE instance %d no longer has a resolvable world."), Request.PieInstanceId));
		}
		if (!Request.WorldPath.IsEmpty() && !PathEqualsExact(Request.WorldPath, Snapshot->WorldPath))
		{
			return Fail(
				EFailure::WorldStale,
				FString::Printf(
					TEXT("PIE instance %d now resolves to world '%s', not the requested world '%s'; the instance was reused."),
					Request.PieInstanceId,
					*Snapshot->WorldPath,
					*Request.WorldPath));
		}

		AActor* Match = nullptr;
		int32 MatchCount = 0;
		TArray<FString> CandidatePaths;
		int32 ScannedActorCount = 0;
		bool bActorScanTruncated = false;
		const FString WantedGuid = NormalizeGuidText(Request.ActorGuid);
		for (TActorIterator<AActor> It(Snapshot->World); It; ++It)
		{
			++ScannedActorCount;
			if (ScannedActorCount > MaxScannedActors)
			{
				bActorScanTruncated = true;
				break;
			}
			AActor* Candidate = *It;
			if (Candidate == nullptr)
			{
				continue;
			}
			bool bMatches = false;
			if (!Request.ActorPath.IsEmpty())
			{
				bMatches = PathEqualsExact(Candidate->GetPathName(), Request.ActorPath);
			}
			else if (!Request.ActorGuid.IsEmpty())
			{
				bMatches = CompactGuidDigits(Candidate->GetActorGuid()) == WantedGuid
					|| CompactGuidDigits(Candidate->GetActorInstanceGuid()) == WantedGuid;
			}
			else if (!Request.ActorName.IsEmpty())
			{
				bMatches = PathEqualsExact(Candidate->GetName(), Request.ActorName);
			}
			else
			{
				bMatches = PathEqualsExact(Candidate->GetActorLabel(false), Request.ActorLabel);
			}
			if (!bMatches)
			{
				continue;
			}
			++MatchCount;
			if (Match == nullptr)
			{
				Match = Candidate;
			}
			if (CandidatePaths.Num() < MaxAmbiguousCandidates)
			{
				CandidatePaths.Add(Candidate->GetPathName());
			}
		}

		if (bActorScanTruncated)
		{
			return Fail(
				EFailure::ScanTruncated,
				FString::Printf(
					TEXT("The bounded Actor scan stopped after %d Actors in PIE instance %d before uniqueness could be proven; narrow the request or use actorGuid."),
					MaxScannedActors,
					Request.PieInstanceId));
		}
		if (MatchCount == 0 || Match == nullptr)
		{
			return Fail(
				EFailure::ActorNotFound,
				FString::Printf(
					TEXT("No Actor in PIE instance %d (world '%s', %s) matches the requested identity."),
					Request.PieInstanceId,
					*Snapshot->WorldPath,
					*Snapshot->NetModeName));
		}
		if (MatchCount > 1)
		{
			return Fail(
				EFailure::ActorAmbiguous,
				FString::Printf(
					TEXT("The Actor identity matches %d Actors in PIE instance %d; use actorPath or actorGuid. Bounded candidates: %s"),
					MatchCount,
					Request.PieInstanceId,
					*JoinCandidatePaths(CandidatePaths)));
		}
		if (Match->IsActorBeingDestroyed())
		{
			return Fail(
				EFailure::ActorNotFound,
				FString::Printf(
					TEXT("The matched Actor '%s' is being destroyed in PIE instance %d."),
					*Match->GetPathName(),
					Request.PieInstanceId));
		}
		if (!Request.ActorClassPath.IsEmpty()
			&& (Match->GetClass() == nullptr
				|| !PathEqualsExact(Match->GetClass()->GetPathName(), Request.ActorClassPath)))
		{
			return Fail(
				EFailure::ActorStale,
				FString::Printf(
					TEXT("The Actor '%s' in PIE instance %d is class '%s', not the expected '%s'."),
					*Match->GetPathName(),
					Request.PieInstanceId,
					Match->GetClass() != nullptr ? *Match->GetClass()->GetPathName() : TEXT(""),
					*Request.ActorClassPath));
		}

		const TCHAR* ResolvedBy = !Request.ActorPath.IsEmpty()
			? TEXT("actorPath")
			: (!Request.ActorGuid.IsEmpty()
				? TEXT("actorGuid")
				: (!Request.ActorName.IsEmpty() ? TEXT("actorName") : TEXT("actorLabel")));
		OutResult = DescribeActor(*Snapshot, Match, Request, ResolvedBy);
		return true;
	}
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildPieWorldContextsResult(
	const TSharedPtr<FJsonObject>& Params) const
{
	int32 MaxContexts = UEAgentKitPieRuntime::DefaultMaxContexts;
	double RequestedMaxContexts = 0.0;
	if (Params.IsValid() && Params->TryGetNumberField(TEXT("maxContexts"), RequestedMaxContexts))
	{
		MaxContexts = static_cast<int32>(RequestedMaxContexts);
	}
	return UEAgentKitPieRuntime::BuildWorldContextsJson(MaxContexts);
}

bool FUEAgentKitEditorBridge::TryInspectPieActorResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	using namespace UEAgentKitPieRuntime;

	OutResult.Reset();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();
	if (!Params.IsValid())
	{
		OutErrorCode = FailureCode(EFailure::InvalidRequest);
		OutErrorMessage = TEXT("editor.inspectPieActor requires an exact pieInstanceId and one Actor identity.");
		return false;
	}

	FActorInspectionRequest Request;
	double PieInstanceId = 0.0;
	if (!Params->TryGetNumberField(TEXT("pieInstanceId"), PieInstanceId))
	{
		OutErrorCode = FailureCode(EFailure::InvalidRequest);
		OutErrorMessage = TEXT("pieInstanceId must be the exact PIE instance id reported by editor.getPieWorldContexts (0 is the primary instance).");
		return false;
	}
	Request.PieInstanceId = static_cast<int32>(PieInstanceId);
	Params->TryGetStringField(TEXT("worldPath"), Request.WorldPath);
	Params->TryGetStringField(TEXT("actorPath"), Request.ActorPath);
	Params->TryGetStringField(TEXT("actorGuid"), Request.ActorGuid);
	Params->TryGetStringField(TEXT("actorName"), Request.ActorName);
	Params->TryGetStringField(TEXT("actorLabel"), Request.ActorLabel);
	Params->TryGetStringField(TEXT("actorClassPath"), Request.ActorClassPath);
	double NumberValue = 0.0;
	if (Params->TryGetNumberField(TEXT("maxComponents"), NumberValue))
	{
		Request.MaxComponents = static_cast<int32>(NumberValue);
	}
	if (Params->TryGetNumberField(TEXT("maxAssetsPerComponent"), NumberValue))
	{
		Request.MaxComponentAssets = static_cast<int32>(NumberValue);
	}
	return TryDescribePieActor(Request, OutResult, OutErrorCode, OutErrorMessage);
}
